"""
Storage module for handling audio file storage using S3-compatible storage.
"""

import os
import logging
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from typing import Tuple

logger = logging.getLogger(__name__)

class S3Storage:
    def __init__(self):
        """Initialize S3 client with environment variables."""
        self.client = None
        
        # Debug log all environment variables (without sensitive values)
        logger.info("Checking S3 environment variables:")
        for var in ['S3_PODCAST_REGION', 'S3_PODCAST_ENDPOINT', 'S3_PODCAST_BUCKET', 'S3_PODCAST_PUBLIC_URL']:
            value = os.getenv(var)
            logger.info(f"  {var}: {'set' if value else 'not set'}")
        logger.info("  S3_PODCAST_ACCESS_KEY: " + ('set' if os.getenv('S3_PODCAST_ACCESS_KEY') else 'not set'))
        logger.info("  S3_PODCAST_SECRET_KEY: " + ('set' if os.getenv('S3_PODCAST_SECRET_KEY') else 'not set'))
        
        self.bucket = os.getenv('S3_PODCAST_BUCKET')
        self.public_url = os.getenv('S3_PODCAST_PUBLIC_URL')
        
        # Check if all required environment variables are set
        required_vars = {
            'S3_PODCAST_REGION': os.getenv('S3_PODCAST_REGION'),
            'S3_PODCAST_ENDPOINT': os.getenv('S3_PODCAST_ENDPOINT'),
            'S3_PODCAST_ACCESS_KEY': os.getenv('S3_PODCAST_ACCESS_KEY'),
            'S3_PODCAST_SECRET_KEY': os.getenv('S3_PODCAST_SECRET_KEY'),
            'S3_PODCAST_BUCKET': self.bucket,
            'S3_PODCAST_PUBLIC_URL': self.public_url
        }
        
        missing_vars = [var for var, value in required_vars.items() if not value]
        if missing_vars:
            logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
            return
        
        try:
            logger.info(f"Initializing S3 client with endpoint: {required_vars['S3_PODCAST_ENDPOINT']}")
            self.client = boto3.client(
                's3',
                region_name=required_vars['S3_PODCAST_REGION'],
                endpoint_url=required_vars['S3_PODCAST_ENDPOINT'],
                aws_access_key_id=required_vars['S3_PODCAST_ACCESS_KEY'],
                aws_secret_access_key=required_vars['S3_PODCAST_SECRET_KEY'],
                config=Config(s3={'addressing_style': 'path'})
            )
            
            # Test the connection by listing the bucket
            self.client.list_objects_v2(Bucket=self.bucket, MaxKeys=1)
            logger.info("Successfully initialized and tested S3 client")
        except Exception as e:
            logger.error(f"Failed to initialize S3 client: {str(e)}")
            self.client = None

    def check_connection(self) -> Tuple[bool, str]:
        """Check if S3 connection is working."""
        if not self.client:
            return False, "not_configured"
        
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True, "connected"
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == '404':
                return False, "bucket_not_found"
            return False, f"error: {str(e)}"
        except Exception as e:
            return False, f"error: {str(e)}"

    def upload_file(self, file_path: str, audio_id: str) -> str:
        """Upload a file to S3 and return its public URL."""
        if not self.client:
            missing_vars = [var for var, value in {
                'S3_PODCAST_REGION': os.getenv('S3_PODCAST_REGION'),
                'S3_PODCAST_ENDPOINT': os.getenv('S3_PODCAST_ENDPOINT'),
                'S3_PODCAST_ACCESS_KEY': os.getenv('S3_PODCAST_ACCESS_KEY'),
                'S3_PODCAST_SECRET_KEY': os.getenv('S3_PODCAST_SECRET_KEY'),
                'S3_PODCAST_BUCKET': self.bucket,
                'S3_PODCAST_PUBLIC_URL': self.public_url
            }.items() if not value]
            raise Exception(f"S3 client not initialized. Missing environment variables: {', '.join(missing_vars)}")
        
        try:
            # Generate a unique object name
            object_name = f"{audio_id}/{os.path.basename(file_path)}"
            
            # Upload the file with public-read ACL
            with open(file_path, 'rb') as f:
                self.client.upload_fileobj(
                    f,
                    self.bucket,
                    object_name,
                    ExtraArgs={
                        'ACL': 'public-read',
                        'ContentType': 'audio/mpeg'
                    }
                )
            
            # Return the public URL
            return f"{self.public_url}/{object_name}"
        except Exception as e:
            logger.error(f"Failed to upload to S3: {str(e)}")
            raise Exception(f"Failed to upload to S3: {str(e)}")

    def delete_file(self, audio_id: str) -> bool:
        """Delete a file from S3."""
        if not self.client:
            missing_vars = [var for var, value in {
                'S3_PODCAST_REGION': os.getenv('S3_PODCAST_REGION'),
                'S3_PODCAST_ENDPOINT': os.getenv('S3_PODCAST_ENDPOINT'),
                'S3_PODCAST_ACCESS_KEY': os.getenv('S3_PODCAST_ACCESS_KEY'),
                'S3_PODCAST_SECRET_KEY': os.getenv('S3_PODCAST_SECRET_KEY'),
                'S3_PODCAST_BUCKET': self.bucket,
                'S3_PODCAST_PUBLIC_URL': self.public_url
            }.items() if not value]
            raise Exception(f"S3 client not initialized. Missing environment variables: {', '.join(missing_vars)}")
        
        try:
            # List objects with the given prefix
            response = self.client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=f"{audio_id}/"
            )
            
            # Delete all objects with the prefix
            if 'Contents' in response:
                objects_to_delete = [{'Key': obj['Key']} for obj in response['Contents']]
                if objects_to_delete:
                    self.client.delete_objects(
                        Bucket=self.bucket,
                        Delete={'Objects': objects_to_delete}
                    )
            
            return True
        except Exception as e:
            logger.error(f"Failed to delete from S3: {str(e)}")
            return False

# Create a singleton instance
s3_storage = S3Storage() 