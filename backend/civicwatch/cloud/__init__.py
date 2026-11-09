"""Cloud deployment layer: dashboard API, connector ingestion and storage glue.

Importing this package never pulls in PyTorch, OpenCV or Ultralytics — the
Vercel functions only handle metadata, incidents, settings and evidence URLs.
Inference stays on the local connector.
"""
