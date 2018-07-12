'''
Created on March 20, 2018

@author: Alejandro Molina
'''
import logging
from collections import deque
from enum import Enum

try:
    from time import perf_counter
except:
    from time import time

    perf_counter = time

import numpy as np

from spn.algorithms.TransformStructure import Prune
from spn.algorithms.Validity import is_valid
from spn.structure.Base import Product, Sum, assign_ids


class Operation(Enum):
    CREATE_LEAF = 1
    SPLIT_COLUMNS = 2
    SPLIT_ROWS = 3
    NAIVE_FACTORIZATION = 4
    REMOVE_UNINFORMATIVE_FEATURES = 5


def get_next_operation(min_instances_slice=100):
    def next_operation(data, scope, no_clusters=False, no_independencies=False, is_first=False, cluster_first=True,
                       cluster_univariate=False):

        minimalFeatures = len(scope) == 1
        minimalInstances = data.shape[0] <= min_instances_slice

        if minimalFeatures:
            if minimalInstances or no_clusters:
                return Operation.CREATE_LEAF, None
            else:
                if cluster_univariate:
                    return Operation.SPLIT_ROWS, None
                else:
                    return Operation.CREATE_LEAF, None

        uninformative_features_idx = np.var(data[:, 0:len(scope)], 0) == 0
        ncols_zero_variance = np.sum(uninformative_features_idx)
        if ncols_zero_variance > 0:
            if ncols_zero_variance == data.shape[1]:
                return Operation.NAIVE_FACTORIZATION, None
            else:
                return Operation.REMOVE_UNINFORMATIVE_FEATURES, np.arange(len(scope))[
                    uninformative_features_idx].tolist()

        if minimalInstances or (no_clusters and no_independencies):
            return Operation.NAIVE_FACTORIZATION, None

        if no_independencies:
            return Operation.SPLIT_ROWS, None

        if no_clusters:
            return Operation.SPLIT_COLUMNS, None

        if is_first:
            if cluster_first:
                return Operation.SPLIT_ROWS, None
            else:
                return Operation.SPLIT_COLUMNS, None

        return Operation.SPLIT_COLUMNS, None

    return next_operation


def default_slicer(data, cols):
    if len(cols) == 1:
        return data[:, cols[0]].reshape((-1, 1))

    return data[:, cols]


def learn_structure(dataset, ds_context, split_rows, split_cols, create_leaf, next_operation=get_next_operation(),
                    initial_scope=None, data_slicer=default_slicer):
    assert dataset is not None
    assert ds_context is not None
    assert split_rows is not None
    assert split_cols is not None
    assert create_leaf is not None
    assert next_operation is not None

    root = Product()
    root.children.append(None)

    if initial_scope is None:
        initial_scope = list(range(dataset.shape[1]))

    tasks = deque()
    tasks.append((dataset, root, 0, initial_scope, False, False))

    while tasks:

        local_data, parent, children_pos, scope, no_clusters, no_independencies = tasks.popleft()

        operation, op_params = next_operation(local_data, scope, no_clusters=no_clusters,
                                              no_independencies=no_independencies,
                                              is_first=(parent is root))

        logging.debug('OP: {} on slice {} (remaining tasks {})'.format(operation, local_data.shape, len(tasks)))

        if operation == Operation.REMOVE_UNINFORMATIVE_FEATURES:

            node = Product()
            node.scope.extend(scope)
            parent.children[children_pos] = node

            rest_scope = set(range(len(scope)))
            for col in op_params:
                rest_scope.remove(col)
                node.children.append(None)
                tasks.append((data_slicer(local_data, [col]), node, len(node.children) - 1, [scope[col]], True, True))

            node.children.append(None)
            c_pos = len(node.children) - 1

            next_final = False

            if len(rest_scope) == 1:
                next_final = True

            rest_scope = list(rest_scope)

            tasks.append((data_slicer(local_data, rest_scope), node, c_pos, rest_scope, next_final, next_final))

            continue

        elif operation == Operation.SPLIT_ROWS:

            split_start_t = perf_counter()
            data_slices = split_rows(local_data, ds_context, scope)
            split_end_t = perf_counter()
            logging.debug(
                '\t\tfound {} row clusters (in {:.5f} secs)'.format(len(data_slices), split_end_t - split_start_t))

            if len(data_slices) == 1:
                tasks.append((local_data, parent, children_pos, scope, True, False))
                continue

            node = Sum()
            node.scope.extend(scope)
            parent.children[children_pos] = node

            for data_slice, scope_slice, proportion in data_slices:
                assert isinstance(scope_slice, list), "slice must be a list"

                node.children.append(None)
                node.weights.append(proportion)
                tasks.append((data_slice, node, len(node.children) - 1, scope, False, False))

            continue

        elif operation == Operation.SPLIT_COLUMNS:

            split_start_t = perf_counter()
            data_slices = split_cols(local_data, ds_context, scope)
            split_end_t = perf_counter()
            logging.debug('\t\tfound {} col clusters (in {:.5f} secs)'.format(len(data_slices),
                                                                              split_end_t - split_start_t))

            if len(data_slices) == 1:
                tasks.append((local_data, parent, children_pos, scope, False, True))
                continue

            node = Product()
            node.scope.extend(scope)
            parent.children[children_pos] = node

            for data_slice, scope_slice, _ in data_slices:
                assert isinstance(scope_slice, list), "slice must be a list"

                node.children.append(None)
                tasks.append((data_slice, node, len(node.children) - 1, scope_slice, False, False))
            continue

        elif operation == Operation.NAIVE_FACTORIZATION:
            node = Product()
            node.scope.extend(scope)
            parent.children[children_pos] = node

            split_start_t = perf_counter()
            for col in range(len(scope)):
                node.children.append(None)
                tasks.append((data_slicer(local_data, [col]), node, len(node.children) - 1, [scope[col]], True, True))
            split_end_t = perf_counter()

            logging.debug('\t\tsplit {} columns (in {:.5f} secs)'.format(len(scope), split_end_t - split_start_t))

            continue

        elif operation == Operation.CREATE_LEAF:

            leaf_start_t = perf_counter()
            node = create_leaf(local_data, ds_context, scope)
            parent.children[children_pos] = node
            leaf_end_t = perf_counter()

            logging.debug('\t\t created leaf {} for scope={} (in {:.5f} secs)'.format(node.__class__.__name__,
                                                                                      scope,
                                                                                      leaf_end_t - leaf_start_t))

        else:
            raise Exception('Invalid operation: ' + operation)

    node = root.children[0]
    assign_ids(node)

    node = Prune(node)
    valid, err = is_valid(node)
    assert valid, "invalid spn: " + err

    return node
