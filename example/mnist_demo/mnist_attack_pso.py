# Copyright 2019 Huawei Technologies Co., Ltd
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
import sys
import time

import numpy as np
from mindspore import Tensor
from mindspore import context
from mindspore.train.serialization import load_checkpoint, load_param_into_net
from scipy.special import softmax

from lenet5_net import LeNet5
from mindarmour import BlackModel
from mindarmour.adv_robustness.attacks.black.pso_attack import PSOAttack
from mindarmour.adv_robustness.evaluations import AttackEvaluate
from mindarmour.utils.logger import LogUtil


sys.path.append("..")
from data_processing import generate_mnist_dataset

LOGGER = LogUtil.get_instance()
LOGGER.set_level('INFO')
TAG = 'PSO_Attack'


class ModelToBeAttacked(BlackModel):
    """model to be attack"""

    def __init__(self, network):
        super(ModelToBeAttacked, self).__init__()
        self._network = network

    def predict(self, inputs):
        """predict"""
        result = self._network(Tensor(inputs.astype(np.float32)))
        return result.asnumpy()


def test_pso_attack_on_mnist():
    """
    PSO-Attack test
    """
    # upload trained network
    ckpt_name = './trained_ckpt_file/checkpoint_lenet-10_1875.ckpt'
    net = LeNet5()
    load_dict = load_checkpoint(ckpt_name)
    load_param_into_net(net, load_dict)

    # get test data
    data_list = "./MNIST_unzip/test"
    batch_size = 32
    ds = generate_mnist_dataset(data_list, batch_size=batch_size)

    # prediction accuracy before attack
    model = ModelToBeAttacked(net)
    batch_num = 3  # the number of batches of attacking samples
    test_images = []
    test_labels = []
    predict_labels = []
    i = 0
    for data in ds.create_tuple_iterator():
        i += 1
        images = data[0].astype(np.float32)
        labels = data[1]
        test_images.append(images)
        test_labels.append(labels)
        pred_labels = np.argmax(model.predict(images), axis=1)
        predict_labels.append(pred_labels)
        if i >= batch_num:
            break
    predict_labels = np.concatenate(predict_labels)
    true_labels = np.concatenate(test_labels)
    accuracy = np.mean(np.equal(predict_labels, true_labels))
    LOGGER.info(TAG, "prediction accuracy before attacking is : %s", accuracy)

    # attacking
    attack = PSOAttack(model, bounds=(0.0, 1.0), pm=0.5, sparse=True)
    start_time = time.clock()
    success_list, adv_data, query_list = attack.generate(
        np.concatenate(test_images), np.concatenate(test_labels))
    stop_time = time.clock()
    LOGGER.info(TAG, 'success_list: %s', success_list)
    LOGGER.info(TAG, 'average of query times is : %s', np.mean(query_list))
    pred_logits_adv = model.predict(adv_data)
    # rescale predict confidences into (0, 1).
    pred_logits_adv = softmax(pred_logits_adv, axis=1)
    pred_labels_adv = np.argmax(pred_logits_adv, axis=1)
    accuracy_adv = np.mean(np.equal(pred_labels_adv, true_labels))
    LOGGER.info(TAG, "prediction accuracy after attacking is : %s",
                accuracy_adv)
    test_labels_onehot = np.eye(10)[np.concatenate(test_labels)]
    attack_evaluate = AttackEvaluate(np.concatenate(test_images),
                                     test_labels_onehot, adv_data,
                                     pred_logits_adv)
    LOGGER.info(TAG, 'mis-classification rate of adversaries is : %s',
                attack_evaluate.mis_classification_rate())
    LOGGER.info(TAG, 'The average confidence of adversarial class is : %s',
                attack_evaluate.avg_conf_adv_class())
    LOGGER.info(TAG, 'The average confidence of true class is : %s',
                attack_evaluate.avg_conf_true_class())
    LOGGER.info(TAG, 'The average distance (l0, l2, linf) between original '
                     'samples and adversarial samples are: %s',
                attack_evaluate.avg_lp_distance())
    LOGGER.info(TAG, 'The average structural similarity between original '
                     'samples and adversarial samples are: %s',
                attack_evaluate.avg_ssim())
    LOGGER.info(TAG, 'The average costing time is %s',
                (stop_time - start_time)/(batch_num*batch_size))


if __name__ == '__main__':
    # device_target can be "CPU", "GPU" or "Ascend"
    context.set_context(mode=context.GRAPH_MODE, device_target="CPU")
    test_pso_attack_on_mnist()
