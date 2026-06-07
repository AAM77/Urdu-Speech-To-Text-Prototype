# AWS S3 Object Store Verification

Use this guide for Stage 8.1.1 verification of the `S3ObjectStore` adapter
against a real or staging AWS S3 bucket.

## Environment

Required:

```bash
export RUN_S3_OBJECT_STORE_SMOKE=1
export AWS_S3_OBJECT_STORE_BUCKET=your-staging-bucket
export AWS_REGION=us-east-1
```

Credential options:

- Prefer an IAM role or workload identity when running from AWS.
- For local staging checks, set both `AWS_ACCESS_KEY_ID` and
  `AWS_SECRET_ACCESS_KEY`.
- Do not set only one static credential; the adapter rejects partial static
  credentials so boto3 does not enter an ambiguous auth mode.

Optional server-side encryption:

```bash
export OBJECT_STORE_SERVER_SIDE_ENCRYPTION=AES256
```

For KMS:

```bash
export OBJECT_STORE_SERVER_SIDE_ENCRYPTION=aws:kms
export OBJECT_STORE_SSE_KMS_KEY_ID=arn:aws:kms:us-east-1:123456789012:key/<key-id>
```

The API and processor pass these settings to direct uploads, artifact writes,
presigned upload URLs, and multipart upload creation without changing the
provider-neutral `ObjectStore` port.

## IAM Permissions

The API role needs permissions for upload URL creation, direct upload handling,
artifact download URLs, and metadata checks:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:ListBucket",
        "s3:AbortMultipartUpload",
        "s3:ListBucketMultipartUploads",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": [
        "arn:aws:s3:::your-staging-bucket",
        "arn:aws:s3:::your-staging-bucket/*"
      ]
    }
  ]
}
```

The processor role needs the same object permissions because it materializes
uploads, writes artifacts, and removes temporary objects. If KMS encryption is
enabled, both roles also need:

```json
{
  "Effect": "Allow",
  "Action": [
    "kms:Encrypt",
    "kms:Decrypt",
    "kms:GenerateDataKey"
  ],
  "Resource": "arn:aws:kms:us-east-1:123456789012:key/<key-id>"
}
```

Scope the bucket and key ARN to the deployment environment. Do not share one
production bucket policy with local or staging credentials.

## Smoke Test

Run the safe integration target with the AWS smoke flag enabled:

```bash
RUN_S3_OBJECT_STORE_SMOKE=1 make test-integration
```

The AWS smoke writes one object under a random `smoke/aws/<uuid>/` prefix,
reads it, checks metadata, generates a signed download URL, lists the prefix,
and deletes the prefix in a `finally` block.

If the smoke fails after writing, manually inspect and delete only the specific
`smoke/aws/<uuid>/` prefix from the error output or test logs. Do not delete
shared prefixes such as `uploads/`, `artifacts/`, or `tmp/` during verification.
