"""
FastAPI implementation for Podcastify podcast generation service.

This module provides REST endpoints for podcast generation and audio serving,
with configuration management and temporary file handling.
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, JSONResponse
import os
import yaml
from typing import Dict, Any
from pathlib import Path
from ..client import generate_podcast
from ..storage import s3_storage, supabase_client
import uvicorn
import logging
import sys
import signal
import tempfile
# Configure logging to stdout
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_base_config() -> Dict[Any, Any]:
    config_path = Path(__file__).parent.parent / "conversation_config.yaml"
    try:
        with open(config_path, 'r') as file:
            return yaml.safe_load(file)
    except Exception as e:
        print(f"Warning: Could not load base config: {e}")
        return {}

def merge_configs(base_config: Dict[Any, Any], user_config: Dict[Any, Any]) -> Dict[Any, Any]:
    """Merge user configuration with base configuration, preferring user values."""
    merged = base_config.copy()
    
    # Handle special cases for nested dictionaries
    if 'text_to_speech' in merged and 'text_to_speech' in user_config:
        merged['text_to_speech'].update(user_config.get('text_to_speech', {}))
    
    # Update top-level keys
    for key, value in user_config.items():
        if key != 'text_to_speech':  # Skip text_to_speech as it's handled above
            if value is not None:  # Only update if value is not None
                merged[key] = value
                
    return merged

def handle_sigterm(signum, frame):
    logger.info("Received SIGTERM, shutting down gracefully.")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)

print("=== fast_app.py is being loaded ===")

app = FastAPI()
print("=== FastAPI app object created ===")

TEMP_DIR = os.path.join(os.path.dirname(__file__), "temp_audio")
os.makedirs(TEMP_DIR, exist_ok=True)

@app.middleware("http")
async def verify_token(request: Request, call_next):
    # Skip token verification for health check endpoint
    if request.url.path == "/health":
        return await call_next(request)
        
    token = request.headers.get("X-Podpod-Access-Token")
    if not token or token != os.getenv("PODPOD_API_ACCESS_TOKEN"):
        return JSONResponse(
            status_code=401,
            content={"message": "Invalid or missing access token"}
        )
    return await call_next(request)

@app.post("/generate")
async def generate_podcast_endpoint(data: dict):
    """"""
    import time
    start_time = time.time()
    request_id = f"req_{int(time.time())}_{hash(str(data))}"
    logger.info(f"🔥 NEW REQUEST [{request_id}] - podcast_id: {data.get('podcast_id', 'not_provided')}")
    
    try:
        # Load base configuration
        base_config = load_base_config()
        
        # Get TTS model and its configuration from base config
        tts_model = data.get('tts_model', base_config.get('text_to_speech', {}).get('default_tts_model', 'openai'))
        tts_base_config = base_config.get('text_to_speech', {}).get(tts_model, {})

        # Get voices (use user-provided voices or fall back to defaults)
        voices = data.get('voices', {})
        default_voices = tts_base_config.get('default_voices', {})

        logger.info(f"Using TTS model: {tts_model}")
        logger.info(f"Voices - Question: {voices.get('question', default_voices.get('question'))}, Answer: {voices.get('answer', default_voices.get('answer'))}")
        
        # Handle text input
        text_input = None
        if 'text' in data:
            text_data = data['text']
            if isinstance(text_data, list):
                text_input = '\n\n---------\n\n'.join(str(text) for text in text_data if text)
                logger.info(f"Processing {len(text_data)} text inputs")
            elif isinstance(text_data, str):
                text_input = text_data
                logger.info("Processing single text input")
            else:
                raise ValueError("Text input must be a string or array of strings")
        
        # Get URLs
        urls = data.get('urls', [])
        if urls:
            logger.info(f"Processing {len(urls)} URLs")

        if text_input and urls:
            logger.info("Processing both URLs and text content")
        elif text_input:
            logger.info("Processing text content only")
        elif urls:
            logger.info("Processing URLs only")
        else:
            logger.warning("No URLs or text content provided")
        
        # Prepare user configuration
        user_config = {
            'creativity': float(data.get('creativity', base_config.get('creativity', 0.7))),
            'conversation_style': data.get('conversation_style', base_config.get('conversation_style', [])),
            'roles_person1': data.get('roles_person1', base_config.get('roles_person1')),
            'roles_person2': data.get('roles_person2', base_config.get('roles_person2')),
            'dialogue_structure': data.get('dialogue_structure', base_config.get('dialogue_structure', [])),
            'podcast_name': data.get('name', base_config.get('podcast_name')),
            'podcast_tagline': data.get('tagline', base_config.get('podcast_tagline')),
            'output_language': data.get('output_language', base_config.get('output_language', 'English')),
            'user_instructions': data.get('user_instructions', base_config.get('user_instructions', '')),
            'engagement_techniques': data.get('engagement_techniques', base_config.get('engagement_techniques', [])),
            'text_to_speech': {
                'default_tts_model': tts_model,
                'model': tts_base_config.get('model'),
                'default_voices': {
                    'question': voices.get('question', default_voices.get('question')),
                    'answer': voices.get('answer', default_voices.get('answer'))
                }
            }
        }

        # Merge configurations
        conversation_config = merge_configs(base_config, user_config)

        # Generate podcast
        result = generate_podcast(
            urls=urls,
            text=text_input,
            conversation_config=conversation_config,
            tts_model=tts_model,
            longform=bool(data.get('is_long_form', False)),
        )

        # Handle the result based on whether podcast_id is provided
        if isinstance(result, str) and os.path.isfile(result):
            if data.get('podcast_id'):
                # Add intro and upload to S3 if podcast_id is provided
                audio_with_intro = addIntroToAudio(result)
                
                # Get audio metadata before upload
                metadata = getAudioMetadata(audio_with_intro)
                
                # Upload to S3
                audio_url = s3_storage.upload_file(audio_with_intro, data['podcast_id'])
                
                # Update Supabase with completion status and audio metadata
                try:
                    supabase_client.update_podcast_completion(
                        podcast_id=data['podcast_id'],
                        audio_url=audio_url,
                        audio_length=metadata['audio_length'],
                        audio_content_type=metadata['audio_content_type'],
                        audio_file_size=metadata['audio_file_size']
                    )
                    logger.info(f"Updated podcast {data['podcast_id']} in Supabase")
                except Exception as e:
                    logger.error(f"Failed to update podcast {data['podcast_id']} in Supabase: {str(e)}")
                    # Don't fail the request if Supabase update fails
                
                os.remove(audio_with_intro)  # Clean up the temporary file
                end_time = time.time()
                logger.info(f"✅ [{request_id}] SUCCESS - Duration: {end_time - start_time:.1f}s, Audio uploaded to S3")
                return JSONResponse(content={"audio_url": audio_url})
            else:
                # Return raw audio data if no podcast_id
                with open(result, 'rb') as audio_file:
                    audio_data = audio_file.read()
                    os.remove(result)  # Clean up the temporary file
                    end_time = time.time()
                    logger.info(f"✅ [{request_id}] SUCCESS - Duration: {end_time - start_time:.1f}s, Audio: {len(audio_data)} bytes")
                    return Response(
                        content=audio_data,
                        media_type="audio/mpeg",
                        headers={
                            "Content-Type": "audio/mpeg",
                            "Content-Length": str(len(audio_data))
                        }
                    )
        elif hasattr(result, 'audio_path'):
            if data.get('podcast_id'):
                # Add intro and upload to S3 if podcast_id is provided
                audio_with_intro = addIntroToAudio(result.audio_path)
                
                # Get audio metadata before upload
                metadata = getAudioMetadata(audio_with_intro)
                
                # Upload to S3
                audio_url = s3_storage.upload_file(audio_with_intro, data['podcast_id'])
                
                # Update Supabase with completion status and audio metadata
                try:
                    supabase_client.update_podcast_completion(
                        podcast_id=data['podcast_id'],
                        audio_url=audio_url,
                        audio_length=metadata['audio_length'],
                        audio_content_type=metadata['audio_content_type'],
                        audio_file_size=metadata['audio_file_size']
                    )
                    logger.info(f"Updated podcast {data['podcast_id']} in Supabase")
                except Exception as e:
                    logger.error(f"Failed to update podcast {data['podcast_id']} in Supabase: {str(e)}")
                    # Don't fail the request if Supabase update fails
                
                os.remove(audio_with_intro)  # Clean up the temporary file
                end_time = time.time()
                logger.info(f"✅ [{request_id}] SUCCESS - Duration: {end_time - start_time:.1f}s, Audio uploaded to S3")
                return JSONResponse(content={"audio_url": audio_url})
            else:
                # Return raw audio data if no podcast_id
                with open(result.audio_path, 'rb') as audio_file:
                    audio_data = audio_file.read()
                    os.remove(result.audio_path)  # Clean up the temporary file
                    end_time = time.time()
                    logger.info(f"✅ [{request_id}] SUCCESS - Duration: {end_time - start_time:.1f}s, Audio: {len(audio_data)} bytes")
                    return Response(
                        content=audio_data,
                        media_type="audio/mpeg",
                        headers={
                            "Content-Type": "audio/mpeg",
                            "Content-Length": str(len(audio_data))
                        }
                    )
        else:
            raise HTTPException(status_code=500, detail="Invalid result format")

    except Exception as e:
        end_time = time.time()
        error_message = str(e)
        logger.error(f"❌ [{request_id}] ERROR - Duration: {end_time - start_time:.1f}s, Error: {error_message}")
        
        # Update Supabase with failure status if podcast_id is provided
        if data.get('podcast_id'):
            try:
                supabase_client.update_podcast_status(
                    podcast_id=data['podcast_id'],
                    status='failed',
                    failed_reason=error_message
                )
                logger.info(f"Updated podcast {data['podcast_id']} status to failed in Supabase")
            except Exception as supabase_error:
                logger.error(f"Failed to update podcast {data['podcast_id']} failure status in Supabase: {str(supabase_error)}")
                # Don't fail the request if Supabase update fails
        
        raise HTTPException(status_code=500, detail=error_message)

@app.get("/health")
async def healthcheck():
    logger.info("/health endpoint called")
    try:
        # Check if temp directory exists and is writable
        if not os.path.exists(TEMP_DIR):
            os.makedirs(TEMP_DIR, exist_ok=True)
        
        # Try to write a test file
        test_file = os.path.join(TEMP_DIR, "health_check.txt")
        with open(test_file, "w") as f:
            f.write("health check")
        os.remove(test_file)
        
        # Check S3 connection if configured
        is_connected, s3_status = s3_storage.check_connection()
        
        # Check Supabase connection if configured
        supabase_connected, supabase_status = supabase_client.check_connection()
        
        return {
            "status": "healthy",
            "temp_dir": TEMP_DIR,
            "temp_dir_writable": True,
            "s3_status": s3_status,
            "supabase_status": supabase_status,
            "environment": {
                "python_version": os.sys.version,
                "working_directory": os.getcwd(),
                "environment_variables": {
                    "OPENAI_API_KEY": bool(os.getenv("OPENAI_API_KEY")),
                    "GEMINI_API_KEY": bool(os.getenv("GEMINI_API_KEY")),
                    "S3_PODCAST_REGION": bool(os.getenv("S3_PODCAST_REGION")),
                    "S3_PODCAST_ENDPOINT": bool(os.getenv("S3_PODCAST_ENDPOINT")),
                    "S3_PODCAST_ACCESS_KEY": bool(os.getenv("S3_PODCAST_ACCESS_KEY")),
                    "S3_PODCAST_SECRET_KEY": bool(os.getenv("S3_PODCAST_SECRET_KEY")),
                    "S3_PODCAST_BUCKET": bool(os.getenv("S3_PODCAST_BUCKET")),
                    "S3_PODCAST_PUBLIC_URL": bool(os.getenv("S3_PODCAST_PUBLIC_URL")),
                    "SUPABASE_URL": bool(os.getenv("SUPABASE_URL")),
                    "SUPABASE_ANON_KEY": bool(os.getenv("SUPABASE_ANON_KEY")),
                    "SUPABASE_KEY": bool(os.getenv("SUPABASE_KEY"))
                }
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

def getAudioMetadata(audio_file_path: str) -> dict:
    """
    Get metadata from audio file.
    
    Args:
        audio_file_path: Path to the audio file
        
    Returns:
        Dict containing audio metadata
    """
    from pydub import AudioSegment
    import os
    
    try:
        # Load audio file
        audio = AudioSegment.from_file(audio_file_path)
        
        # Get file size
        file_size = str(os.path.getsize(audio_file_path))
        
        # Get audio length in seconds
        length_seconds = len(audio) / 1000  # pydub returns milliseconds
        audio_length = str(int(length_seconds))
        
        # Determine content type based on file extension
        file_extension = os.path.splitext(audio_file_path)[1].lower()
        content_type_map = {
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.m4a': 'audio/mp4',
            '.aac': 'audio/aac',
            '.ogg': 'audio/ogg'
        }
        content_type = content_type_map.get(file_extension, 'audio/mpeg')
        
        logger.info(f"Audio metadata - Length: {audio_length}s, Size: {file_size} bytes, Type: {content_type}")
        
        return {
            'audio_length': audio_length,
            'audio_file_size': file_size,
            'audio_content_type': content_type
        }
        
    except Exception as e:
        logger.error(f"Failed to get audio metadata: {str(e)}")
        return {
            'audio_length': '',
            'audio_file_size': '',
            'audio_content_type': 'audio/mpeg'
        }

def addIntroToAudio(audio_file_path: str) -> str:
    """
    Prepend intro audio to the generated podcast audio.
    
    Args:
        audio_file_path: Path to the generated podcast audio file
        
    Returns:
        Path to the new audio file with intro prepended
    """
    from pydub import AudioSegment
    
    intro_path = os.path.join(os.path.dirname(__file__), "intro.wav")
    
    # Check if intro file exists
    if not os.path.exists(intro_path):
        logger.warning(f"Intro file not found at {intro_path}, returning original audio")
        return audio_file_path
    
    try:
        # Load audio files
        intro = AudioSegment.from_wav(intro_path)
        podcast = AudioSegment.from_file(audio_file_path)
        
        # Concatenate intro + podcast
        combined = intro + podcast
        
        # Create temporary file for the combined audio
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir=TEMP_DIR) as temp_file:
            temp_path = temp_file.name
        
        # Export combined audio
        combined.export(temp_path, format="mp3")
        
        logger.info(f"Successfully added intro to audio. Original: {audio_file_path}, Combined: {temp_path}")
        
        # Clean up the original file since we have a new combined one
        if os.path.exists(audio_file_path):
            os.remove(audio_file_path)
            
        return temp_path
        
    except Exception as e:
        logger.error(f"Failed to add intro to audio: {str(e)}")
        return audio_file_path

if __name__ == "__main__":
    logger.info("Starting FastAPI application...")
    logger.info(f"Current working directory: {os.getcwd()}")
    logger.info(f"Environment variables loaded: {bool(os.getenv('OPENAI_API_KEY'))}")
    
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8080))
    
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run(app, host=host, port=port)
