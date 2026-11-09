"""Human-readable labels shared by the API, reports and the local pipeline.

These live in their own module so the cloud API can use them without importing
NumPy/OpenCV (which ``detect.base`` pulls in on the inference side).
"""

from __future__ import annotations

VEHICLE_CLASSES: frozenset[str] = frozenset({"car", "motorcycle", "bus", "truck", "bicycle"})
PERSON_CLASSES: frozenset[str] = frozenset({"person"})
BULKY_CLASSES: frozenset[str] = frozenset({"car", "bus", "truck", "motorcycle"})

VEHICLE_LABELS = {
    "car": "Car",
    "motorcycle": "Motorcycle",
    "bus": "Bus",
    "truck": "Truck",
    "bicycle": "Bicycle",
    "person": "Pedestrian",
}

CATEGORY_LABELS = {
    "illegal_parking": "Potential illegal parking",
    "double_parking": "Potential double parking",
    "footpath_obstruction": "Potential footpath obstruction",
    "restricted_entry": "Potential restricted-zone entry",
    "pothole": "Potential pothole",
    "garbage": "Potential garbage accumulation",
}

STATUS_LABELS = {
    "detected": "Detected",
    "pending_review": "Pending review",
    "verified": "Verified",
    "dismissed": "Dismissed",
    "forwarded": "Forwarded",
    "resolved": "Resolved",
}
