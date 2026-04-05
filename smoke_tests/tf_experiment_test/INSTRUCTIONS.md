# TensorFlow Experiment Guide

This folder is intentionally minimal.
It contains exactly one instruction file so it can be used as a clean document-reading test case.

## Goal

Run a small, repeatable TensorFlow experiment and record enough information so the result can be compared later.

## Recommended Experiment Structure

1. Define one clear question.
   Example: does changing the learning rate improve validation accuracy on this dataset?

2. Change only one main variable at a time.
   Good variables to test:
   - learning rate
   - batch size
   - optimizer
   - number of layers
   - dropout rate

3. Keep everything else fixed.
   This makes the result easier to interpret.

## Basic Setup Checklist

- Choose a dataset.
- Split data into training, validation, and test sets.
- Fix random seeds if possible.
- Write down:
  - dataset name
  - model version
  - TensorFlow version
  - hardware used
  - important hyperparameters

## Minimal Experiment Loop

For each run:

1. Load and preprocess the data.
2. Build the model.
3. Compile the model with a defined optimizer, loss, and metrics.
4. Train on the training set.
5. Evaluate on the validation set.
6. Save the key metrics.
7. Compare the run with previous runs.

## What To Record

Record at least the following:

- experiment name
- date and time
- hypothesis
- changed variable
- fixed variables
- training loss
- validation loss
- validation accuracy or task metric
- final conclusion

## Example Questions

- Which learning rate works best for this model?
- Does adding dropout reduce overfitting?
- Does a larger batch size speed up training without hurting quality?
- Does data augmentation improve validation performance?

## Good Practices

- Start with a very small experiment first.
- Confirm the pipeline runs end to end before doing larger runs.
- Prefer short, comparable runs over many uncontrolled changes.
- Save both metrics and the exact config used.
- If a result is surprising, repeat the run before drawing a conclusion.

## Common Mistakes

- Changing multiple variables in one run.
- Comparing runs that used different datasets or splits.
- Forgetting to record the exact hyperparameters.
- Judging the model only from training metrics.
- Running long experiments before confirming the setup is correct.

## Suggested Beginner Workflow

1. Train a simple baseline model.
2. Record the baseline metrics.
3. Change one variable.
4. Run again.
5. Compare against the baseline.
6. Keep the better setting and test the next variable.

## Expected Output Style For Summaries

If another tool or assistant reads this file, a good summary should explain:

- the purpose of the experiment workflow
- the recommended step-by-step loop
- the most important tracking and comparison rules
