import os
import json
import pickle
import logging
import argparse
import warnings
import numpy as np
import pandas as pd
import xgboost as xgb
from tqdm import tqdm
from numpy.random import default_rng
from collections import OrderedDict, Counter

from sklearn.naive_bayes import GaussianNB
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import StratifiedKFold
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, roc_auc_score, average_precision_score, confusion_matrix

from pyteap.signals.bvp import acquire_bvp, get_bvp_features
from pyteap.signals.gsr import acquire_gsr, get_gsr_features
from pyteap.signals.hst import acquire_hst, get_hst_features
from pyteap.signals.ecg import get_ecg_features
from logutils import init_logger


def load_segments(segments_dir):
    segments = {}

    # for each participant
    for pid in os.listdir(segments_dir):
        segments.setdefault(int(pid), [])
        froot = os.path.join(segments_dir, pid)

        # for segments for a participant
        for fname in os.listdir(froot):
            # get labels, segment index, and path to json file
            labels = fname.split('-')[-1].split('.')[0]
            idx = int(fname.split('-')[1])
            fpath = os.path.join(froot, fname)

            # load json file and save to dict of pid: [segments = (idx, segment, labels)]
            with open(fpath) as f:
                seg = json.load(f)
                segments[int(pid)].append((idx, seg, labels))

    # return dict sorted by pid
    return OrderedDict(sorted(segments.items(), key=lambda x: x[0]))


def get_features(sig, sr, sigtype):
    if sigtype == 'bvp':
        features = get_bvp_features(sig, sr)
    elif sigtype == 'eda':
        features = get_gsr_features(acquire_gsr(sig, sr, conversion=1e6), sr)
    elif sigtype == 'temp':
        features = get_hst_features(acquire_hst(sig, sr), sr)
    elif sigtype == 'ecg':
        features = get_ecg_features(sig)
    return features


def get_data_rolling(segments, n, labeltype, majority):
    X, y = {}, {}

    # for each participant
    for pid, segs in segments.items():
        segs = sorted(segs, key=lambda x: x[0])
        pbar = tqdm(range(len(segs) - n), desc=f'Subject {pid:02d}', ascii=True, dynamic_ncols=True)

        curr_X, curr_y = [], []
        for i in pbar:
            # get n consecutive segments from i-th segment
            curr_segs = segs[i:i + n]

            features = []
            # get features
            for sigtype, sr in [('bvp', 64), ('eda', 4), ('temp', 4), ('ecg', 1)]:
                sig = np.concatenate([sigs[sigtype] for _, sigs, _ in curr_segs])
                features.extend(get_features(sig, sr, sigtype))

            # skip if one or more feature is NaN
            if np.isnan(features).any():
                logging.getLogger('default').warning('One or more feature is NaN, skipped.')
                continue
            
            if labeltype == 's':
                curr_a = [int(labels[0]) for _, _, labels in curr_segs]
                curr_v = [int(labels[1]) for _, _, labels in curr_segs]
            elif labeltype == 'p':
                curr_a = [int(labels[2]) for _, _, labels in curr_segs]
                curr_v = [int(labels[3]) for _, _, labels in curr_segs]
            elif labeltype == 'e':
                curr_a = [int(labels[4]) for _, _, labels in curr_segs]
                curr_v = [int(labels[5]) for _, _, labels in curr_segs]
            elif labeltype == 'sp':
                curr_a = [np.sum([int(labels[0]), int(labels[2])]) for _, _, labels in curr_segs]
                curr_v = [np.sum([int(labels[1]), int(labels[3])]) for _, _, labels in curr_segs]
            
            # take majority label
            if majority:
                a_values, a_counts = np.unique(curr_a, return_counts=True)
                v_values, v_counts = np.unique(curr_v, return_counts=True)
                a_val = a_values[np.argmax(a_counts)]
                v_val = v_values[np.argmax(v_counts)]
            # or take label of the last segment
            else:
                a_val, v_val = curr_a[-1], curr_v[-1]

            curr_X.append(features)
            if labeltype != 'sp':
                curr_y.append([int(a_val > 2), int(v_val > 2)])
            else:
                curr_y.append([int(a_val > 5), int(v_val > 5)])

        # stack features for current participant and apply standardization
        X[pid] = StandardScaler().fit_transform(np.stack(curr_X))
        y[pid] = np.stack(curr_y)

    return X, y


def get_data_discrete(segments, n, labeltype, majority):
    X, y, z = {}, {}, {}

    # for each participant
    for pid, segs in segments.items():
        segs = sorted(segs, key=lambda x: x[0])
        pbar = tqdm(segs, desc=f'For subject {pid:02d}', ascii=True, dynamic_ncols=True)

        curr_X, curr_y, curr_segs = [], [], {}

        # for each segment
        time = []
        for idx, signals, labels in pbar:
            # get labels and add to buffer
            s_a, s_v = int(labels[0]), int(labels[1])
            p_a, p_v = int(labels[2]), int(labels[3])
            e_a, e_v = int(labels[4]), int(labels[5])

            if labeltype == 's':
                curr_segs.setdefault('a', []).append(s_a)
                curr_segs.setdefault('v', []).append(s_v)
            elif labeltype == 'p':
                curr_segs.setdefault('a', []).append(p_a)
                curr_segs.setdefault('v', []).append(p_v)
            elif labeltype == 'e':
                curr_segs.setdefault('a', []).append(e_a)
                curr_segs.setdefault('v', []).append(e_v)
            elif labeltype == 'sp':
                curr_segs.setdefault('a', []).append(np.sum([s_a, p_a]))
                curr_segs.setdefault('v', []).append(np.sum([s_v, p_v]))

            # get signals and add to buffer
            for sigtype, sr in [('bvp', 64), ('eda', 4), ('temp', 4), ('ecg', 1)]:
                curr_segs.setdefault(sigtype, []).append(signals[sigtype])

                # if n segments are in buffer
                if len(curr_segs[sigtype]) == n:
                    # concat signals and get features
                    sig = np.concatenate(curr_segs.pop(sigtype))
                    features = get_features(sig, sr, sigtype)
                    curr_segs.setdefault('features', []).append(features)

            # if features are in the buffer, pop features and labels
            if 'features' in curr_segs:
                features = np.concatenate(curr_segs.pop('features'))
                # skip if one or more feature is NaN
                if np.isnan(features).any():
                    logging.getLogger('default').warning('One or more feature is NaN, skipped.')
                    continue

                # take majority label
                if majority:
                    a_values, a_counts = np.unique(curr_segs.pop('a'), return_counts=True)
                    v_values, v_counts = np.unique(curr_segs.pop('v'), return_counts=True)
                    a_val = a_values[np.argmax(a_counts)]
                    v_val = v_values[np.argmax(v_counts)]
                # or take label of the last segment
                else:
                    a_val = curr_segs.pop('a')[-1]
                    v_val = curr_segs.pop('v')[-1]

                time.append(idx * 5)
                curr_X.append(features)
                if labeltype != 'sp':
                    curr_y.append([int(a_val > 2), int(v_val > 2)])
                else:
                    curr_y.append([int(a_val > 5), int(v_val > 5)])
                pbar.set_postfix({'processed': idx // n})

        # stack features for current participant and apply standardization
        #if (pid == 32): 
        #   print("Skipped subject 32")
        #   continue;
        X[pid] = StandardScaler().fit_transform(np.stack(curr_X))
        y[pid] = np.stack(curr_y)
        z[pid] = time
        
    return X, y, z


def prepare_kemocon(segments_dir, n, labeltype, majority, rolling):
    # load segments
    pid_to_segments = load_segments(segments_dir)

    # extract features and labels
    if rolling:
        X, y = get_data_rolling(pid_to_segments, n, labeltype, majority)
    else:
        X, y, z  = get_data_discrete(pid_to_segments, n, labeltype, majority)

    return X, y, z


# deprecated auroc and ap for compatibility with multiclass classification
def get_results(y_test, preds, probs=None):
    acc = accuracy_score(y_test, preds)
    # bacc = balanced_accuracy_score(y_test, preds, adjusted=False)
    f1 = f1_score(y_test, preds, average='weighted')
    # auroc = roc_auc_score(y_test, probs, average='weighted')
    # ap = average_precision_score(y_test, probs, average='weighted')

    # return {'acc.': acc, 'bacc.': bacc, 'f1': f1, 'auroc': auroc, 'ap': ap}
    return {'acc.': acc, 'f1': f1}


def pred_majority(majority, y_test):
    preds = np.repeat(majority, y_test.size)
    # probs = np.repeat(majority, y_test.size)
    return get_results(y_test, preds)


def pred_random(y_classes, y_test, rng, ratios):
    preds = rng.choice(y_classes, y_test.size, replace=True, p=ratios)
    # if ratios is not None:
    #     probs = np.where(preds == 1, ratios[1], ratios[0])
    # else:
    #     probs = np.repeat(0.5, y_test.size)
    return get_results(y_test, preds)

def load_model(model_name):
    with open(model_name, 'rb') as f:
        return pickle.load(f)

def save_model(model, model_name):
    with open(model_name, 'wb') as f:
        pickle.dump(model, f)

def pred_gnb(X_train, y_train, X_test, y_test, model_path):
    clf = GaussianNB() 
    if (os.path.exists(model_path)):
        clf = load_model(model_path)
    else: 
        clf.fit(X_train, y_train)
    preds = clf.predict(X_test)
    # probs = clf.predict_proba(X_test)[:, 1]

    save_model(clf, model_path)

    return get_results(y_test, preds)

ploted = True

def pred_xgb(X_train, y_train, X_test, y_test, seed, gpu, target):
    # load data into DMatrix
    model_path = '../trained_models/xgb_test' + target + '.model'
    #model_path = '../trained_models/xgb_new' + target + '.model'
    dtrain = xgb.DMatrix(X_train, label=y_train)
    dtest = xgb.DMatrix(X_test, label=y_test)
    # set parameters
    params = {
        'booster': 'gbtree',
        'verbosity': 1,
        'max_depth': 6,
        'learning_rate': 0.3,
        'objective': 'multi:softmax',
        'eval_metric': 'mlogloss' if target == 'multiclass' else 'logloss',
        'num_class': 4 if target == 'multiclass' else 2,
        'seed': seed,
    }

    # train model and predict
    num_round = 100
    xgb_clf = xgb.XGBClassifier(
        n_estimators=num_round,
        booster='gbtree',
        verbosity= 1,
        max_depth= 6,
        learning_rate= 0.3,
        objective= 'multi:softmax',
        eval_metric= 'mlogloss' if target == 'multiclass' else 'logloss',
        num_class= 4 if target == 'multiclass' else 2,
        seed = seed, 
        )

    xgb_clf = xgb.train(params, dtrain, num_round)
    if (os.path.exists(model_path)):
        print("Loading model from: ", model_path)
        xgb_clf = load_model(model_path)
    #preds = bst.predict(dtest)

    #xgb_clf.fit(X_train, y_train) 
    #proba = xgb_clf.predict_proba(X_test)
    #preds = xgb_clf.predict(X_test)
    #plot prbabilities

    preds = xgb_clf.predict(dtest)
    #save the model
    save_model(xgb_clf, model_path)

    # return results
    return get_results(y_test, preds)
    
def plot_proba(proba, y_test):
    import matplotlib.pyplot as plt
    classes = ['la-lv', 'la-hv', 'ha-lv', 'ha-hv']
    for i in range(4):
        plt.plot(proba[:,i], label=classes[i])
    plt.xlabel('Test samples')
    plt.ylabel('Probability')
    plt.legend()
    plt.show()

def pred_rforest(X_train, y_train, X_test, y_test, seed, target):
    from sklearn.ensemble import RandomForestClassifier
    clf = RandomForestClassifier(max_depth=2, random_state=0)
    clf.fit(X_train, y_train)
    preds = clf.predict(X_test)
    # probs = clf.predict_proba(X_test)[:, 1]
    print(get_results(y_test, preds))
    return get_results(y_test, preds)


def get_baseline_kfold(X, y, seed, target, n_splits, shuffle, gpu):
    # initialize random number generator and fold generator
    rng = default_rng(seed)
    skf = StratifiedKFold(n_splits=n_splits, shuffle=shuffle, random_state=seed)

    # aggregated features and labels
    X = np.concatenate(list(X.values()))
    y = np.concatenate(list(y.values()))
    logging.getLogger('default').info(f'Dataset size: {X.shape}')
    # get labels corresponding to target class
    if target == 'arousal':
        y = y[:, 0]
    elif target == 'valence':
        y = y[:, 1]
    elif target == 'multiclass':
        classes = np.unique(y, axis=0).tolist()
        y = np.fromiter(map(lambda x: classes.index(x.tolist()), y), dtype=np.int)

    results = {}
    # for each fold, split train & test and get classification results
    for i, (train_idx, test_idx) in enumerate(skf.split(X, y)):
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]

        y_classes, y_counts = np.unique(y_train, return_counts=True)
        majority = y_classes[np.argmax(y_counts)]
        class_ratios = y_counts / y_train.size
        n_classes = len(y_classes)

        results[i+1] = {
            'Random': pred_random(y_classes, y_test, rng, ratios=np.repeat(1/n_classes, n_classes)),
            'Majority': pred_majority(majority, y_test),
            'Class ratio': pred_random(y_classes, y_test, rng, ratios=class_ratios),
            'Gaussian NB': pred_gnb(X_train, y_train, X_test, y_test, '../trained_models/gnb_' + target + '.model'),
            'XGBoost': pred_xgb(X_train, y_train, X_test, y_test, seed, gpu, target),
            'Rforest': pred_rforest(X_train, y_train, X_test, y_test, seed, target),
        }
    # return results as table
    results = {(fold, classifier): values for (fold, _results) in results.items() for (classifier, values) in _results.items()}
    results_table = pd.DataFrame.from_dict(results, orient='index').stack().unstack(level=1).rename_axis(['Fold', 'Metric'])
    return results_table[['Random', 'Majority', 'Class ratio', 'Gaussian NB', 'XGBoost', 'Rforest']]


def get_baseline_loso(X, y, seed, target, n_splits, shuffle, gpu):
    # initialize random number generator
    rng = default_rng(seed)

    classes = np.unique(np.concatenate(list(y.values())), axis=0).tolist()

    results = {}
    # for each participant split train & test
    for pid in X.keys():
        X_train, X_test = np.concatenate([v for k, v in X.items() if k != pid]), X[pid]
        y_train, y_test = np.concatenate([v for k, v in y.items() if k != pid]), y[pid]
        # get labels corresponding to target class
        if target == 'arousal':
            y_train, y_test = y_train[:, 0], y_test[:, 0]
        elif target == 'valence':
            y_train, y_test = y_train[:, 1], y_test[:, 1]
        elif target == 'multiclass': 
            y_train = np.fromiter(map(lambda x: classes.index(x.tolist()), y_train), dtype=np.int)
            y_test = np.fromiter(map(lambda x: classes.index(x.tolist()), y_test), dtype=np.int)


        # skip current user if there aren't both labels (0, 1) in the test set
        #print(len(y_test), len(X_test), len(y_train), len(X_train))
        #if len(Counter(y_test)) != 2:
        #    continue

        # get majority label and class ratios
        y_classes, y_counts = np.unique(y_train, return_counts=True)
        majority = y_classes[np.argmax(y_counts)]
        class_ratios = y_counts / y_train.size

        # get classification results
        results[pid] = {
            'Random': pred_random(y_classes, y_test, rng, ratios=class_ratios),
            'Majority': pred_majority(majority, y_test),
            'Class ratio': pred_random(y_classes, y_test, rng, ratios=class_ratios),
            'Gaussian NB': pred_gnb(X_train, y_train, X_test, y_test, '../trained_models/gnb_' + target + '.model'),
            'XGBoost': pred_xgb(X_train, y_train, X_test, y_test, seed, gpu, target),
            'Rforest': pred_rforest(X_train, y_train, X_test, y_test, seed, target),
        }
    results = {(pid, classifier): value for (pid, _results) in results.items() for (classifier, value) in _results.items()}
    results_table = pd.DataFrame.from_dict(results, orient='index').stack().unstack(level=1)
    return results_table[['Random', 'Majority', 'Class ratio', 'Gaussian NB', 'XGBoost', 'Rforest']]


def get_baseline(X, y, seed, target, cv, n_splits, shuffle, gpu):
    if cv == 'kfold':
        results = get_baseline_kfold(X, y, seed, target, n_splits, shuffle, gpu)
    elif cv == 'loso':
        results = get_baseline_loso(X, y, seed, target, n_splits, shuffle, gpu)

    return results

def write_features_to_file(features, labels, time):
    classes = np.unique(np.concatenate(list(labels.values())), axis=0).tolist()
    #print(labels)
    dtypes = [('pid', int), ('segs', int), ('labels', int), ('time', int)]
    for pid, segs in features.items():
        y = np.fromiter(map(lambda x: classes.index(x.tolist()), labels[pid]), dtype=np.int)
        data = np.c_[segs, y, time[pid]]

        print("Writing files for subject: ", pid) 
        file = '../../features/' + str(pid) + '.csv';
        np.savetxt(file, data, delimiter=",")

if __name__ == "__main__":
    # initialize parser
    parser = argparse.ArgumentParser(description='Preprocess K-EmoCon dataset and get baseline classification results.')
    parser.add_argument('-r', '--root', type=str, required=True, help='path to the dataset directory')
    parser.add_argument('-tz', '--timezone', type=str, default='UTC', help='a pytz timezone string for logger, default is UTC')
    parser.add_argument('-s', '--seed', type=int, default=0, help='seed for random number generation, default is 0')
    parser.add_argument('-t', '--target', type=str, default='valence', help='target label for classification, must be either "valence" or "arousal"')
    parser.add_argument('-l', '--length', type=int, default=1, help='number of consecutive 5s-signals in one segment, default is 5')
    parser.add_argument('-y', '--label', type=str, default='s', help='type of label to use for classification, must be either "s"=self, "p"=partner, "e"=external, or "sp"=self+partner (default="s")')
    parser.add_argument('--majority', default=True, action='store_true', help='set majority label for segments, default is last')
    parser.add_argument('--rolling', default=False, action='store_true', help='get segments with rolling: e.g., s1=[0:n], s2=[1:n+1], ..., default is no rolling: e.g., s1=[0:n], s2=[n:2n], ...')
    parser.add_argument('--cv', type=str, default='kfold', help='type of cross-validation to perform, must be either "kfold" or "loso"')
    parser.add_argument('--splits', type=int, default=4, help='number of folds for k-fold stratified classification, default is 4')
    parser.add_argument('--shuffle', default=False, action='store_true', help='shuffle data before splitting to folds, default is no shuffle')
    parser.add_argument('--savedir', type=str, default='../results/', help='path to the directory to save classification results')
    parser.add_argument('--gpu', default=False, action='store_true', help='if True, use available GPU for XGBoost')
    args = parser.parse_args()

    # check commandline arguments
    if args.target not in ['valence', 'arousal', 'multiclass']:
        raise ValueError(f'--target must be either "valence" or "arousal" or "multiclass", but given {args.target}')
    elif args.length < 1:
        raise ValueError(f'--length must be greater than 5')
    elif args.label not in ['s', 'p', 'e', 'sp']:
        raise ValueError(f'--label must be either "s", "p", "e", or "sp", but given {args.label}')
    elif args.cv not in ['kfold', 'loso']:
        raise ValueError(f'--cv must be either "kfold" or "loso", but given {args.cv}')

    # initialize default logger
    logger = init_logger(tz=args.timezone)

    # filter these RuntimeWarning messages
    warnings.filterwarnings(action='ignore', message='Mean of empty slice')
    warnings.filterwarnings(action='ignore', message='invalid value encountered in double_scalars')
    warnings.filterwarnings(action='ignore', message='divide by zero encountered in true_divide')
    warnings.filterwarnings(action='ignore', message='invalid value encountered in subtract')

    # get features and labels
    segments_dir = os.path.expanduser(args.root)
    logger.info(f'Processing segments from {segments_dir}, with: seed={args.seed}, target={args.target}, length={args.length*5}s, label={args.label}, majority={args.majority}, rolling={args.rolling}, cv={args.cv}, splits={args.splits}, shuffle={args.shuffle}, gpu={args.gpu}')
    features, labels, time = prepare_kemocon(segments_dir, args.length, args.label, args.majority, args.rolling)
    #write_features_to_file(features, labels, time)
    logger.info('Processing complete.')

    # get classification results
    results = get_baseline(features, labels, args.seed, args.target, args.cv, args.splits, args.shuffle, args.gpu)
    
    # save summary of classification results as csv files
    if args.cv == 'kfold':
        savefile = f'seed={args.seed}_target={args.target}_len={args.length*5}_label={args.label}_{"majority" if args.majority else "last"}_{"rolling" if args.rolling else "discrete"}_k={args.splits}_{"shuffle" if args.shuffle else "no-shuffle"}.csv'
        savepath = os.path.join(args.savedir, savefile)
        results = results.groupby(level='Metric').mean()
        results.to_csv(savepath)
    else:
        savepath = os.path.join(args.savedir, f'{args.target}-loso.csv')
        results.to_csv(savepath)

    print(results)
    logger.info(f'Saved results to {savepath}')
