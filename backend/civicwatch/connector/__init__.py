"""Local connector: runs inference on this Mac and syncs incidents to the cloud.

The connector never accepts inbound connections and never shares camera
credentials. It only makes outbound HTTPS requests to the deployed dashboard,
authenticated with a per-connector bearer token.
"""

from .worker import ConnectorWorker  # noqa: F401
