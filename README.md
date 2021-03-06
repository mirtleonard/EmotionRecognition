# K-EmoCon Supplementary Codes

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.3931963.svg)](https://doi.org/10.5281/zenodo.3931963)
This repository contains supplementary codes for the **K-EmoCon Dataset**.

For the detailed description of the dataset, please refer to:
> Park, C.Y., Cha, N., Kang, S. *et al.* K-EmoCon, a multimodal sensor dataset for continuous emotion recognition in naturalistic conversations. *Sci Data* **7**, 293 (2020). https://doi.org/10.1038/s41597-020-00630-y
---

## Usage
### Installation
```console
$ git clone https://github.com/cheulyop/K-EmoCon_SupplementaryCodes.git
$ cd K-EmoCon_SupplementaryCodes
```

### Preprocessing
```console
$ python preprocess.py --root '/path/to/kemocon_root'
```
Running [preprocess.py](https://github.com/cheulyop/K-EmoCon_SupplementaryCodes/blob/master/preprocess.py) will create 5-second segments containing 4 types of biosignals `['bvp', 'eda', 'temp', 'ecg']` acquired during debates as JSON files to `'/path/to/kemocon_root/segments/'`, under subdirectories corresponding to each participant.

JSON files for biosignal segments will have names with the following pattern: for example, `p01-017-243333.json` indicates that the file is a 17th 5-second biosignal segment for participant 1.

The last 6 digits are multiperspective emotion annotations associated with the segment, in the order of 1) self-arousal, 2) self-valence, 3) partner-arousal, 4) partner-valence, 5) external-arousal, and 6) external-valence.

### Baseline Classification
[baseline.py](https://github.com/cheulyop/K-EmoCon_SupplementaryCodes/blob/master/baseline.py) implements a stratified k-fold and leave-one-subject-out (LOSO) cross-validation for the binary classification of low and high classes (with low = 1--2 and high = 3--5) and the multi-class classification between 4 quadrants (LALV, LAHV, HALV, HAHV), with three heuristic voters (random, majority, and class ratio), [Gaussian NB](https://scikit-learn.org/stable/modules/generated/sklearn.naive_bayes.GaussianNB.html#sklearn-naive-bayes-gaussiannb), and [XGBoost](https://github.com/dmlc/xgboost).

\* `baseline.py` requires [PyTEAP](https://pypi.org/project/PyTEAP/) for feature extraction. You can install it by running: `pip install PyTEAP` in your terminal.

---

## Results
For the classification results, see csv files in the [`results`](https://github.com/Kaist-ICLab/K-EmoCon_SupplementaryCodes/tree/master/results) folder.

---

## Supplementary Codes for the K-EmoCon Dataset Descriptor
* [chauvenet.py](https://github.com/Kaist-ICLab/K-EmoCon_SupplementaryCodes/blob/master/chauvenet.py) - an implementation of [Chauvenet's criterion](https://en.wikipedia.org/wiki/Chauvenet%27s_criterion) for detecting outliers.
* [vote_majority.py](https://github.com/Kaist-ICLab/K-EmoCon_SupplementaryCodes/blob/master/vote_majority.py) - implements a majority voting to get a consensus between external annotations.
* [plotting.py](https://github.com/cheulyop/K-EmoCon_SupplementaryCodes/blob/master/utils/plotting.py) - includes functions to produce the IRR heatmap (Fig. 4) in the K-EmoCon dataset descriptor.
  * `get_annotations` - loads annotations saved as CSV files.
  * `subtract_mode_from_values` - implements mode subtraction.
  * `compute_krippendorff_alpha` - computes Krippendorff's alpha (IRR).
  * `plot_heatmaps` - plots the IRR heatmap.
