import abc
import os
import sys

import cv2
import numpy as np
import tensorflow as tf
from scipy.io import loadmat
import pickle

from confs.conf import DeepFaceConfs
from recognizers.recognizer_base import FaceRecognizer


class FaceRecognizerVGG(FaceRecognizer):
    NAME = 'recognizer_vgg'

    def __init__(self):
        dir_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'vggface')
        filename = 'weight.mat'
        filepath = os.path.join(dir_path, filename)

        if not os.path.exists(filepath):
            raise FileNotFoundError('Weight file not found, path=%s' % filepath)

        data = loadmat(filepath)

        # read meta info
        meta = data['meta']
        classes = meta['classes']
        normalization = meta['normalization']

        self.average_image = np.squeeze(normalization[0][0]['averageImage'][0][0][0][0])
        self.input_hw = tuple(np.squeeze(normalization[0][0]['imageSize'][0][0])[:2])
        self.input_node = tf.placeholder(tf.float32, shape=(None, self.input_hw[0], self.input_hw[1], 3), name='image')
        self.class_names = [str(x[0][0]) for x in classes[0][0]['description'][0][0]]

        # read layer info
        layers = data['layers']
        current = self.input_node
        network = {}
        for layer in layers[0]:
            name = layer[0]['name'][0][0]
            layer_type = layer[0]['type'][0][0]
            if layer_type == 'conv':
                if name[:2] == 'fc':
                    padding = 'VALID'
                else:
                    padding = 'SAME'
                stride = layer[0]['stride'][0][0]
                kernel, bias = layer[0]['weights'][0][0]
                # kernel = np.transpose(kernel, (1, 0, 2, 3))
                bias = np.squeeze(bias).reshape(-1)
                conv = tf.nn.conv2d(current, tf.constant(kernel), strides=(1, stride[0], stride[0], 1), padding=padding)
                current = tf.nn.bias_add(conv, bias)
            elif layer_type == 'relu':
                current = tf.nn.relu(current)
            elif layer_type == 'pool':
                stride = layer[0]['stride'][0][0]
                pool = layer[0]['pool'][0][0]
                current = tf.nn.max_pool(current, ksize=(1, pool[0], pool[1], 1), strides=(1, stride[0], stride[0], 1), padding='SAME')
            elif layer_type == 'softmax':
                current = tf.nn.softmax(tf.reshape(current, [-1, len(self.class_names)]))

            network[name] = current
        self.network = network

        self.graph = tf.get_default_graph()
        self.persistent_sess = tf.Session(graph=self.graph)
        self.db = None

        db_path = DeepFaceConfs.get()['recognizer']['vgg'].get('db', '')
        if db_path:
            with open(os.path.join(dir_path, db_path), 'rb') as f:
                self.db = pickle.load(f)

    def name(self):
        return FaceRecognizerVGG.NAME

    def detect(self, rois):
        new_rois = []
        for roi in rois:
            if roi.shape[0] != self.input_hw[0] or rois.shape[1] != self.input_hw[1]:
                new_roi = cv2.resize(roi, self.input_hw, interpolation=cv2.INTER_AREA)
                new_rois.append(new_roi)
            else:
                new_rois.append(roi)

        probs, feats = self.persistent_sess.run([self.network['prob'], self.network['fc7']], feed_dict={
            self.input_node: new_rois
        })
        feats = [np.squeeze(x) for x in feats]
        if self.db is None:
            names = [[(self.class_names[idx], prop[idx]) for idx in prop.argsort()[-DeepFaceConfs.get()['recognizer']['topk']:][::-1]] for prop in probs]
        else:
            # TODO
            names = []
            for feat in feats:
                scores = []
                for db_name, db_feature in self.db.items():
                    similarity = np.dot(feat / np.linalg.norm(feat, 2), db_feature / np.linalg.norm(db_feature, 2))
                    print(db_name, similarity)
                    scores.append((db_name, similarity))
                scores.sort(key=lambda x: x[1], reverse=True)
                names.append(scores)

        return {
            'output': probs,
            'feature': feats,
            'name': names
        }
