"""Enable and verify bucket encryption before starting the API."""
import os
import boto3

client = boto3.client(
    "s3", endpoint_url="http://seaweedfs:8333", region_name=os.environ.get("HND_S3_REGION", "us-east-1"),
    aws_access_key_id=os.environ["HND_S3_ADMIN_ACCESS_KEY"],
    aws_secret_access_key=os.environ["HND_S3_ADMIN_SECRET_KEY"],
)
bucket = os.environ.get("HND_S3_BUCKET", "hnd-objects")
client.head_bucket(Bucket=bucket)
client.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={
    "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}],
})
key = ".deployment-encryption-check"
try:
    client.put_object(Bucket=bucket, Key=key, Body=b"encryption-check")
    assert client.head_object(Bucket=bucket, Key=key).get("ServerSideEncryption") == "AES256"
    assert client.get_object(Bucket=bucket, Key=key)["Body"].read() == b"encryption-check"
finally:
    client.delete_object(Bucket=bucket, Key=key)
print("Storage encryption verified.")
