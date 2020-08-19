# Copyright 2020 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Membership Inference
"""

import numpy as np

import mindspore as ms
from mindspore.train import Model
import mindspore.nn as nn
import mindspore.context as context
from mindspore import Tensor
from mindarmour.diff_privacy.evaluation.attacker import get_attack_model

def _eval_info(pred, truth, option):
    """
    Calculate the performance according to pred and truth.

    Args:
        pred (numpy.ndarray): Predictions for each sample.
        truth (numpy.ndarray): Ground truth for each sample.
        option(str): Type of evaluation indicators; Possible
            values are 'precision', 'accuracy' and 'recall'.

    Returns:
        float32, Calculated evaluation results.

    Raises:
        ValueError, size of parameter pred or truth is 0.
        ValueError, value of parameter option must be in ["precision", "accuracy", "recall"].
    """
    if pred.size == 0 || truth.size == 0:
        raise ValueError("Size of pred or truth is 0.")

    if option == "accuracy":
        count = np.sum(pred == truth)
        return count / len(pred)
    if option == "precision":
        count = np.sum(pred & truth)
        if np.sum(pred) == 0:
            return -1
        return count / np.sum(pred)
    if option == "recall":
        count = np.sum(pred & truth)
        if np.sum(truth) == 0:
            return -1
        return count / np.sum(truth)

    raise ValueError("The metric value {} is undefined.".format(option))


class MembershipInference:
    """
    Evaluation proposed by Shokri, Stronati, Song and Shmatikov is a grey-box attack.
    The attack requires obtain loss or logits results of training samples.

    References: Reza Shokri, Marco Stronati, Congzheng Song, Vitaly Shmatikov.
    Membership Inference Attacks against Machine Learning Models. 2017.
    arXiv:1610.05820v2 <https://arxiv.org/abs/1610.05820v2>`_

    Args:
        model (Model): Target model.

    Examples:
        >>> # ds_train, eval_train are non-overlapping datasets from training dataset.
        >>> # eval_train, eval_test are non-overlapping datasets from test dataset.
        >>> model = Model(network=net, loss_fn=loss, optimizer=opt, metrics={'acc', 'loss'})
        >>> inference_model = MembershipInference(model)
        >>> config = [{"method": "KNN", "params": {"n_neighbors": [3, 5, 7]}}]
        >>> inference_model.train(ds_train, ds_test, config)
        >>> metrics = ["precision", "recall", "accuracy"]
        >>> result = inference_model.eval(eval_train, eval_test, metrics)

    Raises:
        TypeError: If type of model is not mindspore.train.Model.
    """

    def __init__(self, model):
        if not isinstance(model, Model):
            raise TypeError("Type of model must be {}, but got {}.".format(type(Model), type(model)))
        self.model = model
        self.attack_list = []

    def train(self, dataset_train, dataset_test, attack_config):
        """
        Depending on the configuration, use the incoming data set to train the attack model.
        Save the attack model to self.attack_list.

        Args:
            dataset_train (mindspore.dataset): The training dataset for the target model.
            dataset_test (mindspore.dataset): The test set for the target model.
            attack_config (list): Parameter setting for the attack model.

        Raises:
            ValueError: If the method in attack_config is not in ["LR", "KNN", "RF", "MLPC"].
        """
        features, labels = self._transform(dataset_train, dataset_test)
        for config in attack_config:
            self.attack_list.append(get_attack_model(features, labels, config))

    def eval(self, dataset_train, dataset_test, metrics):
        """
        Evaluate the different privacy of the target model.
        Evaluation indicators shall be specified by metrics.

        Args:
            dataset_train (mindspore.dataset): The training dataset for the target model.
            dataset_test (mindspore.dataset): The test dataset for the target model.
            metrics (Union[list, tuple]): Evaluation indicators. The value of metrics
                must be in ["precision", "accuracy", "recall"]. Default: ["precision"].

        Returns:
            list, Each element contains an evaluation indicator for the attack model.
        """
        result = []
        features, labels = self._transform(dataset_train, dataset_test)
        for attacker in self.attack_list:
            pred = attacker.predict(features)
            item = {}
            for option in metrics:
                item[option] = _eval_info(pred, labels, option)
            result.append(item)
        return result

    def _transform(self, dataset_train, dataset_test):
        """
        Generate corresponding loss_logits feature and new label, and return after shuffle.

        Args:
            dataset_train: The training set for the target model.
            dataset_test: The test set for the target model.

        Returns:
            - numpy.ndarray, Loss_logits features for each sample. Shape is (N, C).
                N is the number of sample. C = 1 + dim(logits).
            - numpy.ndarray, Labels for each sample, Shape is (N,).
        """
        features_train, labels_train = self._generate(dataset_train, 1)
        features_test, labels_test = self._generate(dataset_test, 0)
        features = np.vstack((features_train, features_test))
        labels = np.hstack((labels_train, labels_test))
        shuffle_index = np.array(range(len(labels)))
        np.random.shuffle(shuffle_index)
        features = features[shuffle_index]
        labels = labels[shuffle_index]
        return features, labels

    def _generate(self, dataset_x, label):
        """
        Return a loss_logits features and labels for training attack model.

        Args:
            dataset_x (mindspore.dataset): The dataset to be generate.
            label (int32): Whether dataset_x belongs to the target model.

        Returns:
            - numpy.ndarray, Loss_logits features for each sample. Shape is (N, C).
                N is the number of sample. C = 1 + dim(logits).
            - numpy.ndarray, Labels for each sample, Shape is (N,).
        """
        if context.get_context("device_target") != "Ascend":
            raise RuntimeError("The target device must be Ascend, "
                               "but current is {}.".format(context.get_context("device_target")))
        loss_logits = np.array([])
        for batch in dataset_x.create_dict_iterator():
            batch_data = Tensor(batch['image'], ms.float32)
            batch_labels = Tensor(batch['label'], ms.int32)
            batch_logits = self.model.predict(batch_data)
            loss = nn.SoftmaxCrossEntropyWithLogits(sparse=True, is_grad=False, reduction=None)
            batch_loss = loss(batch_logits, batch_labels).asnumpy()
            batch_logits = batch_logits.asnumpy()

            batch_feature = np.hstack((batch_loss.reshape(-1, 1), batch_logits))
            if loss_logits.size == 0:
                loss_logits = batch_feature
            else:
                loss_logits = np.vstack((loss_logits, batch_feature))

        if label == 1:
            labels = np.ones(len(loss_logits), np.int32)
        elif label == 0:
            labels = np.zeros(len(loss_logits), np.int32)
        else:
            raise ValueError("The value of label must be 0 or 1, but got {}.".format(label))
        return loss_logits, labels
