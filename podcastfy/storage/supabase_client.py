"""
Supabase client module for handling database operations.
"""

import os
import logging
from typing import Dict, Any, Optional, Tuple
from supabase import create_client, Client

logger = logging.getLogger(__name__)

class SupabaseClient:
    def __init__(self):
        """Initialize Supabase client with environment variables."""
        self.client: Optional[Client] = None
        
        # Debug log all environment variables (without sensitive values)
        logger.info("Checking Supabase environment variables:")
        for var in ['SUPABASE_URL', 'SUPABASE_ANON_KEY', 'SUPABASE_KEY']:
            value = os.getenv(var)
            logger.info(f"  {var}: {'set' if value else 'not set'}")
        
        self.url = os.getenv('SUPABASE_URL')
        self.anon_key = os.getenv('SUPABASE_ANON_KEY')
        self.service_key = os.getenv('SUPABASE_KEY')  # Service role key for server-side operations
        
        # Check if required environment variables are set
        required_vars = {
            'SUPABASE_URL': self.url,
            'SUPABASE_KEY': self.service_key  # We'll use the service key for server operations
        }
        
        missing_vars = [var for var, value in required_vars.items() if not value]
        if missing_vars:
            logger.error(f"Missing required Supabase environment variables: {', '.join(missing_vars)}")
            return
        
        try:
            logger.info(f"Initializing Supabase client with URL: {self.url}")
            # Use service key for server-side operations
            self.client = create_client(self.url, self.service_key)
            logger.info("Successfully initialized Supabase client")
        except Exception as e:
            logger.error(f"Failed to initialize Supabase client: {str(e)}")
            self.client = None

    def check_connection(self) -> Tuple[bool, str]:
        """Check if Supabase connection is working."""
        if not self.client:
            return False, "not_configured"
        
        try:
            # Test connection by fetching a simple query
            response = self.client.table('podcasts').select('id').limit(1).execute()
            return True, "connected"
        except Exception as e:
            logger.error(f"Supabase connection check failed: {str(e)}")
            return False, f"error: {str(e)}"

    def update_podcast_completion(
        self, 
        podcast_id: str, 
        audio_url: str,
        audio_length: str,
        audio_content_type: str = "audio/mpeg",
        audio_file_size: str = ""
    ) -> bool:
        """
        Update podcast record with completion status and audio metadata.
        
        Args:
            podcast_id: The ID of the podcast to update
            audio_url: URL of the uploaded audio file
            audio_length: Length of the audio in seconds or formatted string
            audio_content_type: MIME type of the audio file
            audio_file_size: Size of the audio file in bytes
            
        Returns:
            bool: True if update was successful, False otherwise
        """
        if not self.client:
            missing_vars = [var for var, value in {
                'SUPABASE_URL': self.url,
                'SUPABASE_KEY': self.service_key
            }.items() if not value]
            raise Exception(f"Supabase client not initialized. Missing environment variables: {', '.join(missing_vars)}")
        
        try:
            update_data = {
                'status': 'completed',
                'audio_url': audio_url,
                'audio_length': audio_length,
                'audio_content_type': audio_content_type,
                'audio_file_size': audio_file_size
            }
            
            logger.info(f"Updating podcast {podcast_id} with completion data")
            
            response = self.client.table('podcasts').update(update_data).eq('id', podcast_id).execute()
            
            if response.data:
                logger.info(f"Successfully updated podcast {podcast_id}")
                return True
            else:
                logger.warning(f"No rows were updated for podcast {podcast_id}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to update podcast {podcast_id}: {str(e)}")
            raise Exception(f"Failed to update podcast in Supabase: {str(e)}")

    def get_podcast(self, podcast_id: str) -> Optional[Dict[str, Any]]:
        """
        Get podcast record by ID.
        
        Args:
            podcast_id: The ID of the podcast to retrieve
            
        Returns:
            Dict containing podcast data or None if not found
        """
        if not self.client:
            raise Exception("Supabase client not initialized")
        
        try:
            response = self.client.table('podcasts').select('*').eq('id', podcast_id).execute()
            
            if response.data:
                return response.data[0]
            else:
                logger.warning(f"Podcast {podcast_id} not found")
                return None
                
        except Exception as e:
            logger.error(f"Failed to get podcast {podcast_id}: {str(e)}")
            raise Exception(f"Failed to get podcast from Supabase: {str(e)}")

    def update_podcast_status(self, podcast_id: str, status: str, failed_reason: str = None) -> bool:
        """
        Update podcast status.
        
        Args:
            podcast_id: The ID of the podcast to update
            status: New status ('pending', 'scraping', 'converting_text', 'generating_metadata', 
                   'generating_audio', 'failed', 'completed', 'paused')
            failed_reason: Reason for failure (only used when status is 'failed')
            
        Returns:
            bool: True if update was successful, False otherwise
        """
        if not self.client:
            raise Exception("Supabase client not initialized")
        
        try:
            update_data = {'status': status}
            if failed_reason and status == 'failed':
                update_data['failed_reason'] = failed_reason
            
            logger.info(f"Updating podcast {podcast_id} status to {status}")
            
            response = self.client.table('podcasts').update(update_data).eq('id', podcast_id).execute()
            
            if response.data:
                logger.info(f"Successfully updated podcast {podcast_id} status to {status}")
                return True
            else:
                logger.warning(f"No rows were updated for podcast {podcast_id}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to update podcast {podcast_id} status: {str(e)}")
            raise Exception(f"Failed to update podcast status in Supabase: {str(e)}")

# Create a singleton instance
supabase_client = SupabaseClient() 