# @Author: cc
# @Date:   2017-12-02T12:41:34+09:00
# @Email:  guangmingwu2010@gmail.com
# @Filename: main.py
# @Last modified by:   cc
# @Last modified time: 2017-12-14T23:10:21+09:00
# @License: MIT



from __future__ import division
import argparse
import torch
from torch.utils import model_zoo
from torch.autograd import Variable

import models
import utils
import pandas as pd
import matplotlib.pyplot as plt
from data_loader import get_train_test_loader, get_office31_dataloader


CUDA = True if torch.cuda.is_available() else False
LEARNING_RATE = 1e-3
WEIGHT_DECAY = 5e-4
MOMENTUM = 0.9
BATCH_SIZE = [200, 56]
EPOCHS = 50


source_loader = get_office31_dataloader(case='amazon', batch_size=BATCH_SIZE[0])
target_loader = get_office31_dataloader(case='webcam', batch_size=BATCH_SIZE[1])


def CORAL(target, source):
    # input must be Variable
    # return coral loss of target and source
    dim = target.data.shape[1]
    nb_t = target.data.shape[0]
    nb_s = source.data.shape[0]
    ones_t = torch.ones(nb_t).view(1, -1)
    ones_s = torch.ones(nb_s).view(1, -1)
    if CUDA:
        ones_t = ones_t.cuda()
        ones_s = ones_s.cuda()
    ones_t = Variable(ones_t, requires_grad=False)
    ones_s = Variable(ones_s, requires_grad=False)
    tmp_t = ones_t.matmul(target)
    tmp_s = ones_s.matmul(source)
    cov_t = (target.t().matmul(target) - (tmp_t.t().matmul(tmp_t) / nb_t)) / (nb_t - 1)
    cov_s = (source.t().matmul(source) - (tmp_s.t().matmul(tmp_s) / nb_s)) / (nb_s - 1)
    coral = ((cov_t-cov_s)**2).sum()/(4*dim*dim)
    return coral

def train(model, optimizer, epoch, _lambda):
    model.train()

    result = []

    # Expected size : xs -> (batch_size, 3, 300, 300), ys -> (batch_size)
    source, target = list(enumerate(source_loader)), list(enumerate(target_loader))
    train_steps = min(len(source), len(target))

    for batch_idx in range(train_steps):
        _, (source_data, source_label) = source[batch_idx]
        _, (target_data, _) = target[batch_idx]
        if CUDA:
            source_data = source_data.cuda()
            source_label = source_label.cuda()
            target_data = target_data.cuda()

        source_data, source_label = Variable(source_data), Variable(source_label)
        target_data = Variable(target_data)

        optimizer.zero_grad()
        source_outs, target_outs = model(source_data, target_data)

        classification_loss = torch.nn.functional.cross_entropy(source_outs[1], source_label)
        # coral_loss = coral(source_outs[0], target_outs[0])
        coral_loss = CORAL(source_outs[1], target_outs[1])
        sum_loss = _lambda*coral_loss + classification_loss
        sum_loss.backward()

        optimizer.step()

        result.append({
            'epoch': epoch,
            'step': batch_idx + 1,
            'total_steps': train_steps,
            'lambda': _lambda,
            'coral_loss': coral_loss.data[0],
            'classification_loss': classification_loss.data[0],
            'total_loss': sum_loss.data[0]
        })

        # print('Train Epoch: {:2d} [{:2d}/{:2d}]\t'
        #       'Lambda: {:.4f}, Class: {:.6f}, CORAL: {:.6f}, Total_Loss: {:.6f}'.format(
        #           epoch,
        #           batch_idx + 1,
        #           train_steps,
        #           _lambda,
        #           classification_loss.data[0],
        #           coral_loss.data[0],
        #           sum_loss.data[0]
        #       ))

    return result


def test(model, dataset_loader, e, mode='source'):
    model.eval()
    test_loss = 0
    correct = 0
    for data, target in dataset_loader:
        if CUDA:
            data, target = data.cuda(), target.cuda()

        data, target = Variable(data, volatile=True), Variable(target)
        out1, out2 = model(data, data)

        out = out1 if mode == 'source' else out2

        # sum up batch loss
        test_loss += torch.nn.functional.cross_entropy(out[1], target, size_average=False).data[0]

        # get the index of the max log-probability
        pred = out[1].data.max(1, keepdim=True)[1]
        correct += pred.eq(target.data.view_as(pred)).cpu().sum()

    test_loss /= len(dataset_loader.dataset)

    return {
        'epoch': e,
        'average_loss': test_loss,
        'correct': correct,
        'total': len(dataset_loader.dataset),
        'accuracy': 100. * correct / len(dataset_loader.dataset)
    }


# load AlexNet pre-trained model
def load_pretrained(model):
    url = 'https://download.pytorch.org/models/alexnet-owt-4df8aa71.pth'
    pretrained_dict = model_zoo.load_url(url)
    model_dict = model.state_dict()

    # filter out unmatch dict and delete last fc bias, weight
    pretrained_dict = {k: v for k, v in pretrained_dict.items() if k in model_dict}
    # del pretrained_dict['classifier.6.bias']
    # del pretrained_dict['classifier.6.weight']

    model_dict.update(pretrained_dict)
    model.load_state_dict(model_dict)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--load', help='Resume from checkpoint file')
    args = parser.parse_args()

    for with_coral in [False, True]:
        model = models.DeepCORAL(31)
        # support different learning rate according to CORAL paper
        # i.e. 10 times learning rate for the last two fc layers.
        optimizer = torch.optim.SGD([
            {'params': model.sharedNet.parameters()},
            {'params': model.fc.parameters(), 'lr': 10*LEARNING_RATE},
        ], lr=LEARNING_RATE, momentum=MOMENTUM)
    
        if CUDA:
            model = model.cuda()

        load_pretrained(model.sharedNet)
        training_statistic = []
        testing_s_statistic = []
        testing_t_statistic = []
        for e in range(0, EPOCHS):
            if not with_coral:
                _lambda = 0
            else:
                _lambda = 1
            res = train(model, optimizer, e+1, _lambda)
            print('###EPOCH {}: Class: {:.6f}, CORAL: {:.6f}, Total_Loss: {:.6f}'.format(
                e+1,
                sum(row['classification_loss'] / row['total_steps'] for row in res),
                sum(row['coral_loss'] / row['total_steps'] for row in res),
                sum(row['total_loss'] / row['total_steps'] for row in res),
            ))
    
            training_statistic.append(res)
    
            test_source = test(model, source_loader, e)
            test_target = test(model, target_loader, e, mode='target')
            testing_s_statistic.append(test_source)
            testing_t_statistic.append(test_target)
    
            print('###Test Source: Epoch: {}, avg_loss: {:.4f}, Accuracy: {}/{} ({:.2f}%)'.format(
                e+1,
                test_source['average_loss'],
                test_source['correct'],
                test_source['total'],
                test_source['accuracy'],
            ))
            print('###Test Target: Epoch: {}, avg_loss: {:.4f}, Accuracy: {}/{} ({:.2f}%)'.format(
                e+1,
                test_target['average_loss'],
                test_target['correct'],
                test_target['total'],
                test_target['accuracy'],
            ))
    
        test_s_records = pd.DataFrame(testing_s_statistic)
        test_t_records = pd.DataFrame(testing_t_statistic)
        test_s_records.to_csv("source_{0}.csv".format("with_coral" if with_coral else "without_coral"), index=False)
        test_t_records.to_csv("target_{0}.csv".format("with_coral" if with_coral else "without_coral"), index=False)

    s_with = pd.read_csv("source_with_coral.csv")
    s_without = pd.read_csv("source_without_coral.csv")
    t_with = pd.read_csv("target_with_coral.csv")
    t_without = pd.read_csv("target_without_coral.csv")

    plt.figure()
    plt.plot(s_with["epoch"], s_with["accuracy"], "r")
    plt.plot(s_without["epoch"], s_without["accuracy"], "b")
    plt.plot(t_with["epoch"], t_with["accuracy"], "g")
    plt.plot(t_without["epoch"], t_without["accuracy"], "y")
    plt.legend(["source acc, w/ coral","source acc, w/o coral","target acc, w/ coral","target acc, w/o coral"])
    plt.xlabel("Number of EPOCHS")
    plt.ylabel("Accuracy")
    plt.show()