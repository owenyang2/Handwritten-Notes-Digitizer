# Portable launchers

`colab_kaggle_launch.ipynb` is intentionally a thin wrapper around the checked-
in `training.cli` and `benchmarks.runner`. It works on either a Colab or Kaggle
GPU runtime after the repository, CUDA lock, and a user-authorized verified
dataset manifest are supplied. It never treats synthetic fixtures as real
accuracy evidence and reminds the operator to copy checkpoints off ephemeral
storage.
