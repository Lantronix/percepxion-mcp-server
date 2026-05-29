# src/percepxion_mcp/providers/aws.py
import json
import os
from . import CredentialProvider, Credentials

try:
    import boto3  # type: ignore
except ImportError:
    boto3 = None  # type: ignore


class AwsProvider(CredentialProvider):
    """Read credentials from AWS Secrets Manager."""

    def __init__(self) -> None:
        if boto3 is None:
            raise RuntimeError(
                "boto3 is required for the 'aws' provider. "
                "Install with: pip install percepxion-mcp-server[aws]"
            )
        self.secret_name = os.environ["AWS_SECRET_NAME"]
        self.region = os.getenv("AWS_REGION", "us-east-1")

    def get_credentials(self) -> Credentials:
        client = boto3.client("secretsmanager", region_name=self.region)
        resp = client.get_secret_value(SecretId=self.secret_name)
        data = json.loads(resp["SecretString"])
        return {"username": data["username"], "password": data["password"]}
