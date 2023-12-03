import numpy as np
import os, sys, joblib
import matplotlib.pyplot as plt

from sklearn import linear_model
from sklearn import metrics
from sklearn import preprocessing
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import RepeatedStratifiedKFold


def predict():
    if len(sys.argv) <= 1:
        print('file path not given')
        return

    path = sys.argv[1]
    #path = './features/features_mu_ew23_accdata_9_2_1710-1736.csv'
    data = np.loadtxt(path, delimiter=',')

    x_data = data[:, :-1]
    x_data = preprocessing.scale(x_data)

    #loading models
    SEED = 1
    np.random.seed(SEED)
    models = [] 
    #for key in ['baseline', 'logit', 'rf']:
    key = 'rf'
    path = 'trained_models/' + key + '.joblib'
    #models.append((key, joblib.load(path)))
    #_roc_auc = metrics.roc_auc_score(y_data, y_proba[:
    clf = joblib.load(path)
    y_pred = clf.predict(x_data)
    y_proba = clf.predict_proba(x_data)
    y_proba = y_proba[::10]
    plt.plot(y_proba[:, 0], label = 'Positive Emotion')
    plt.plot(y_proba[:, 1], label = 'Negative Emotion')
    plt.xlabel('Sample Index')
    plt.ylabel('confidence')
    plt.legend()
    #plt.show()
    plt.savefig('smartwatch-emotions.png')
    print('smartwatch-emotions.png')    


if  __name__ == "__main__":
    predict()