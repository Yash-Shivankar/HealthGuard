"""Offline training and evaluation for the HealthGuard triage model.

Nothing in this package is imported by the Django app at runtime.  The app
loads a single versioned artefact produced by ``ml.train`` (AUDIT §2.8, §7.2):
training and serving are deliberately separate so a model can be retrained and
swapped without touching or restarting the web tier's code path.
"""
