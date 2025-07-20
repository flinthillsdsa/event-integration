#!/usr/bin/env python3
"""
Action Network to Discord and Google Calendar Integration Service
Dual sync: Action Network → Discord + Google Calendar
Version 6.0 - Removed TeamUp, Google Calendar with hashtag routing
"""

from flask import Flask, request, jsonify
import requests
import json
import os
from datetime import datetime, timezone, timedelta
import logging
import threading
import time
import pytz
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration - set these as environment variables
ACTION_NETWORK_API_KEY = os.environ.get('ACTION_NETWORK_API_KEY')
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
DISCORD_GUILD_ID = int(os.environ.get('DISCORD_GUILD_ID', 0)) if os.environ.get('DISCORD_GUILD_ID') else None

# Google Calendar configuration
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
GOOGLE_DSA_CALENDAR_ID = os.environ.get('GOOGLE_DSA_CALENDAR_ID')
GOOGLE_DIRECT_ACTION_CALENDAR_ID = os.environ.get('GOOGLE_DIRECT_ACTION_CALENDAR_ID')
GOOGLE_EDUCATION_CALENDAR_ID = os.environ.get('GOOGLE_EDUCATION_CALENDAR_ID')
GOOGLE_OUTREACH_CALENDAR_ID = os.environ.get('GOOGLE_OUTREACH_CALENDAR_ID')
GOOGLE_SOCIALS_CALENDAR_ID = os.environ.get('GOOGLE_SOCIALS_CALENDAR_ID')
GOOGLE_STEERING_CALENDAR_ID = os.environ.get('GOOGLE_STEERING_CALENDAR_ID')
GOOGLE_VOLUNTEERING_CALENDAR_ID = os.environ.get('GOOGLE_VOLUNTEERING_CALENDAR_ID')

ACTION_NETWORK_ORG = 'fhdsa'  # Your organization slug

# In-memory storage for event mappings (in production, you'd use a database)
# Format: {action_network_id: {'discord_id': '456', 'google_id': '789', 'google_calendar_id': 'cal@group...', 'last_modified': '2025-06-22T...', 'status': 'confirmed'}}
event_mappings = {}

class ActionNetworkGoogleDiscordSync:
    def __init__(self):
        self.action_network_headers = {
            'OSDI-API-Token': ACTION_NETWORK_API_KEY,
            'Content-Type': 'application/json'
        }
        self.action_network_base_url = 'https://actionnetwork.org/api/v2'
        
        # Initialize Google Calendar service
        self.google_service = self._init_google_calendar()
    
    def _init_google_calendar(self):
        """Initialize Google Calendar API service"""
        if not GOOGLE_SERVICE_ACCOUNT_JSON:
            logger.warning("⚠️ Google Calendar not configured - missing service account JSON")
            return None
        
        try:
            # Parse the JSON credentials
            credentials_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
            
            # Create credentials from service account info
            credentials = service_account.Credentials.from_service_account_info(
                credentials_info,
                scopes=['https://www.googleapis.com/auth/calendar']
            )
            
            # Build the Calendar service
            service = build('calendar', 'v3', credentials=credentials)
            logger.info("✅ Google Calendar API initialized")
            return service
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Google Calendar API: {str(e)}")
            return None
    
    def get_google_calendar_id(self, an_event):
        """
        Determine which Google Calendar to use based on hashtags in Action Network event description
        """
        description = an_event.get('description', '').lower()
        
        # Hashtag to calendar ID mapping
        hashtag_mapping = {
            '#dsa': GOOGLE_DSA_CALENDAR_ID,
            '#action': GOOGLE_DIRECT_ACTION_CALENDAR_ID,
            '#education': GOOGLE_EDUCATION_CALENDAR_ID,
            '#outreach': GOOGLE_OUTREACH_CALENDAR_ID,
            '#social': GOOGLE_SOCIALS_CALENDAR_ID,
            '#steering': GOOGLE_STEERING_CALENDAR_ID,
            '#volunteer': GOOGLE_VOLUNTEERING_CALENDAR_ID
        }
        
        # Check for hashtags anywhere in description
        for hashtag, calendar_id in hashtag_mapping.items():
            if hashtag in description and calendar_id:
                return hashtag, calendar_id
        
        # Default to DSA calendar if no hashtag found and it's configured
        return 'none (defaulted to DSA)', GOOGLE_DSA_CALENDAR_ID if GOOGLE_DSA_CALENDAR_ID else None
    
    def fetch_action_network_events(self, limit=25):
        """
        Fetch events from Action Network API
        """
        try:
            url = f"{self.action_network_base_url}/events"
            params = {
                'limit': limit
            }
            
            logger.info(f"Fetching events from Action Network...")
            response = requests.get(url, headers=self.action_network_headers, params=params)
            
            if response.status_code == 200:
                data = response.json()
                events = data.get('_embedded', {}).get('osdi:events', [])
                logger.info(f"✅ Fetched {len(events)} events from Action Network")
                return events
            else:
                logger.error(f"❌ Failed to fetch Action Network events: {response.status_code} - {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"❌ Error fetching Action Network events: {str(e)}")
            return []
    
    def transform_to_google_event(self, an_event):
        """
        Transform Action Network event to Google Calendar format
        """
        try:
            title = an_event.get('title', 'Untitled Event')
            description = an_event.get('description', '')
            registration_url = an_event.get('browser_url', '')
            
            # Create description for Google Calendar (plain text, no HTML)
            google_description = description
            if registration_url:
                if description:
                    google_description += f'\n\nRegister: {registration_url}'
                else:
                    google_description = f'Register: {registration_url}'
            
            # Handle start/end times with timezone conversion
            start_date = None
            end_date = None
            
            # Action Network events can have multiple start/end times
            if 'start_date' in an_event:
                start_date = an_event['start_date']
            elif 'start_time' in an_event:
                start_date = an_event['start_time']
            
            if 'end_date' in an_event:
                end_date = an_event['end_date']
            elif 'end_time' in an_event:
                end_date = an_event['end_time']
            
            # Convert times from Central to UTC if they don't have timezone info
            def convert_to_utc(time_str):
                if not time_str:
                    return None
                
                try:
                    # Action Network appears to store Central Time with 'Z' suffix incorrectly
                    # The 'Z' suggests UTC, but the times are actually Central Time
                    if time_str.endswith('Z'):
                        # Remove the 'Z' and treat as Central Time
                        dt_str = time_str[:-1]  # Remove 'Z'
                        dt = datetime.fromisoformat(dt_str)
                        
                        # Treat as Central Time
                        central = pytz.timezone('US/Central')
                        dt_central = central.localize(dt)
                        
                        # Convert to UTC
                        dt_utc = dt_central.astimezone(pytz.UTC)
                        
                        # Return in ISO format with Z
                        result = dt_utc.isoformat().replace('+00:00', 'Z')
                        logger.debug(f"🕐 Time conversion: {time_str} (Central) → {result} (UTC)")
                        return result
                    
                    # If it has timezone info (+00:00 format), keep as is
                    elif '+' in time_str or '-' in time_str.split('T')[-1]:
                        return time_str
                    
                    # If no timezone info, assume Central Time
                    else:
                        dt = datetime.fromisoformat(time_str)
                        central = pytz.timezone('US/Central')
                        dt_central = central.localize(dt)
                        dt_utc = dt_central.astimezone(pytz.UTC)
                        return dt_utc.isoformat().replace('+00:00', 'Z')
                    
                except Exception as e:
                    logger.warning(f"⚠️ Error converting time {time_str}: {e}")
                    return time_str  # Return original if conversion fails
            
            start_date = convert_to_utc(start_date)
            end_date = convert_to_utc(end_date)
            
            # If no end time, assume 2 hours after start
            if start_date and not end_date:
                try:
                    start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                    end_dt = start_dt + timedelta(hours=2)
                    end_date = end_dt.isoformat().replace('+00:00', 'Z')
                except:
                    end_date = start_date
            
            # Handle location
            location_str = ""
            location = an_event.get('location', {})
            if location:
                if isinstance(location, dict):
                    venue = location.get('venue', '')
                    address_lines = location.get('address_lines', [])
                    locality = location.get('locality', '')
                    region = location.get('region', '')
                    postal_code = location.get('postal_code', '')
                    
                    parts = []
                    if venue:
                        parts.append(venue)
                    if address_lines:
                        parts.extend(address_lines)
                    if locality:
                        parts.append(locality)
                    if region:
                        parts.append(region)
                    if postal_code:
                        parts.append(postal_code)
                    
                    location_str = ', '.join(parts)
                elif isinstance(location, str):
                    location_str = location
            
            # Google Calendar event format
            google_event = {
                'summary': title,
                'description': google_description,
                'location': location_str,
                'start': {
                    'dateTime': start_date,
                    'timeZone': 'UTC'
                },
                'end': {
                    'dateTime': end_date,
                    'timeZone': 'UTC'
                }
            }
            
            return google_event
            
        except Exception as e:
            logger.error(f"❌ Error transforming event to Google format: {str(e)}")
            return None
    
    def create_google_event(self, an_event):
        """
        Create an event in Google Calendar
        """
        if not self.google_service:
            logger.warning("⚠️ Google Calendar not configured, skipping Google event creation")
            return None, None
        
        try:
            # Determine which calendar to use
            hashtag_used, calendar_id = self.get_google_calendar_id(an_event)
            if not calendar_id:
                logger.warning("⚠️ No Google Calendar ID found for event, skipping")
                return None, None
            
            # Transform to Google Calendar format
            google_event_data = self.transform_to_google_event(an_event)
            if not google_event_data:
                return None, None
            
            # Create the event
            result = self.google_service.events().insert(
                calendarId=calendar_id,
                body=google_event_data
            ).execute()
            
            google_event_id = result['id']
            
            # Log which calendar was used
            calendar_names = {
                GOOGLE_DSA_CALENDAR_ID: "Flint Hills Chapter DSA",
                GOOGLE_DIRECT_ACTION_CALENDAR_ID: "Direct Action",
                GOOGLE_EDUCATION_CALENDAR_ID: "Education",
                GOOGLE_OUTREACH_CALENDAR_ID: "Outreach",
                GOOGLE_SOCIALS_CALENDAR_ID: "Socials",
                GOOGLE_STEERING_CALENDAR_ID: "Steering Committee",
                GOOGLE_VOLUNTEERING_CALENDAR_ID: "Volunteering and Mutual Aid"
            }
            
            calendar_name = calendar_names.get(calendar_id, "Unknown Calendar")
            logger.info(f"📅 Created Google Calendar event: {google_event_data['summary']} → {calendar_name} (hashtag: {hashtag_used})")
            
            return google_event_id, calendar_id
            
        except Exception as e:
            logger.error(f"❌ Error creating Google Calendar event: {str(e)}")
            return None, None
    
    def update_google_event(self, google_event_id, calendar_id, an_event):
        """
        Update an existing event in Google Calendar
        """
        if not self.google_service:
            return None
        
        try:
            # Transform to Google Calendar format
            google_event_data = self.transform_to_google_event(an_event)
            if not google_event_data:
                return None
            
            # Update the event
            result = self.google_service.events().update(
                calendarId=calendar_id,
                eventId=google_event_id,
                body=google_event_data
            ).execute()
            
            logger.info(f"📅 Updated Google Calendar event: {google_event_data['summary']}")
            return google_event_id
            
        except Exception as e:
            logger.error(f"❌ Error updating Google Calendar event: {str(e)}")
            return None
    
    def delete_google_event(self, google_event_id, calendar_id):
        """
        Delete an event from Google Calendar
        """
        if not self.google_service:
            return False
        
        try:
            self.google_service.events().delete(
                calendarId=calendar_id,
                eventId=google_event_id
            ).execute()
            
            logger.info(f"📅 Deleted Google Calendar event ID: {google_event_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error deleting Google Calendar event: {str(e)}")
            return False
    
    def create_discord_event_direct(self, an_event, google_event_data):
        """
        Create a Discord scheduled event using direct REST API calls (no asyncio)
        """
        if not DISCORD_BOT_TOKEN or not DISCORD_GUILD_ID:
            logger.warning("⚠️ Discord bot not configured, skipping Discord event creation")
            return None
        
        try:
            # Convert times to ISO format
            start_time = google_event_data['start']['dateTime']
            end_time = google_event_data['end']['dateTime']
            
            # Create Discord event description (Discord doesn't support HTML)
            description = an_event.get('description', '')
            registration_url = an_event.get('browser_url', '')
            
            # Strip HTML tags from description for Discord
            import re
            if description:
                # Remove HTML tags
                description = re.sub(r'<[^>]+>', '', description)
                # Clean up extra whitespace
                description = re.sub(r'\s+', ' ', description).strip()
            
            if registration_url:
                if description:
                    description += f"\n\nRegister: {registration_url}"
                else:
                    description = f"Register: {registration_url}"
            
            # Limit description to Discord's 1000 character limit
            if len(description) > 1000:
                description = description[:997] + "..."
            
            # Discord API payload
            payload = {
                "name": google_event_data['summary'],
                "description": description,
                "scheduled_start_time": start_time,
                "scheduled_end_time": end_time,
                "privacy_level": 2,  # GUILD_ONLY
                "entity_type": 3,    # EXTERNAL
                "entity_metadata": {
                    "location": google_event_data.get('location', 'TBD')
                }
            }
            
            # Make direct API call to Discord
            headers = {
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json"
            }
            
            url = f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/scheduled-events"
            response = requests.post(url, headers=headers, json=payload)
            
            if response.status_code == 200:
                discord_event = response.json()
                discord_event_id = discord_event['id']
                logger.info(f"🎮 Created Discord event: {google_event_data['summary']} (ID: {discord_event_id})")
                return discord_event_id
            else:
                logger.error(f"❌ Failed to create Discord event: {response.status_code} - {response.text}")
                return None
            
        except Exception as e:
            logger.error(f"❌ Error creating Discord event: {str(e)}")
            return None
    
    def update_discord_event_direct(self, discord_event_id, an_event, google_event_data):
        """
        Update a Discord scheduled event using direct REST API calls
        """
        if not DISCORD_BOT_TOKEN or not DISCORD_GUILD_ID:
            return None
        
        try:
            # Convert times to ISO format
            start_time = google_event_data['start']['dateTime']
            end_time = google_event_data['end']['dateTime']
            
            # Create Discord event description (Discord doesn't support HTML)
            description = an_event.get('description', '')
            registration_url = an_event.get('browser_url', '')
            
            # Strip HTML tags from description for Discord
            import re
            if description:
                # Remove HTML tags
                description = re.sub(r'<[^>]+>', '', description)
                # Clean up extra whitespace
                description = re.sub(r'\s+', ' ', description).strip()
            
            if registration_url:
                if description:
                    description += f"\n\nRegister: {registration_url}"
                else:
                    description = f"Register: {registration_url}"
            
            # Limit description to Discord's 1000 character limit
            if len(description) > 1000:
                description = description[:997] + "..."
            
            # Discord API payload
            payload = {
                "name": google_event_data['summary'],
                "description": description,
                "scheduled_start_time": start_time,
                "scheduled_end_time": end_time,
                "entity_metadata": {
                    "location": google_event_data.get('location', 'TBD')
                }
            }
            
            # Make direct API call to Discord
            headers = {
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json"
            }
            
            url = f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/scheduled-events/{discord_event_id}"
            response = requests.patch(url, headers=headers, json=payload)
            
            if response.status_code == 200:
                logger.info(f"🎮 Updated Discord event: {google_event_data['summary']}")
                return discord_event_id
            else:
                logger.error(f"❌ Failed to update Discord event: {response.status_code} - {response.text}")
                return None
            
        except Exception as e:
            logger.error(f"❌ Error updating Discord event: {str(e)}")
            return None
    
    def delete_discord_event_direct(self, discord_event_id):
        """
        Delete a Discord scheduled event using direct REST API calls
        """
        if not DISCORD_BOT_TOKEN or not DISCORD_GUILD_ID:
            return False
        
        try:
            headers = {
                "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                "Content-Type": "application/json"
            }
            
            url = f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/scheduled-events/{discord_event_id}"
            response = requests.delete(url, headers=headers)
            
            if response.status_code == 204:
                logger.info(f"🎮 Deleted Discord event ID: {discord_event_id}")
                return True
            else:
                logger.error(f"❌ Failed to delete Discord event: {response.status_code} - {response.text}")
                return False
            
        except Exception as e:
            logger.error(f"❌ Error deleting Discord event: {str(e)}")
            return False
    
    def sync_events(self):
        """
        Full dual sync of events: Action Network → Google Calendar + Discord
        """
        try:
            logger.info("🔄 Starting full event sync (Action Network → Google Calendar + Discord)...")
            
            # Fetch events from Action Network
            an_events = self.fetch_action_network_events()
            
            new_events_count = 0
            updated_events_count = 0
            deleted_events_count = 0
            
            # Process each Action Network event
            for an_event in an_events:
                # Try multiple ways to get a unique ID
                event_id = None
                identifiers = an_event.get('identifiers', [])
                
                if identifiers:
                    # Extract ID from identifiers like "action_network:d909fe46-37fb-4e88-b5d5-681c8ecd4ed6"
                    for identifier in identifiers:
                        if isinstance(identifier, str) and ':' in identifier:
                            event_id = identifier.split(':')[-1]
                            break
                        elif isinstance(identifier, str):
                            event_id = identifier
                            break
                
                # Fallback to other ID fields if identifiers don't work
                if not event_id:
                    event_id = an_event.get('id', '')
                
                # Last resort: use browser_url as unique identifier
                if not event_id:
                    browser_url = an_event.get('browser_url', '')
                    if browser_url:
                        event_id = browser_url.split('/')[-1]  # Extract event slug from URL
                
                if not event_id:
                    logger.warning(f"⚠️ No valid ID found for event: {an_event.get('title', 'No title')}")
                    continue
                
                title = an_event.get('title', 'No title')
                status = an_event.get('status', 'confirmed')
                modified_date = an_event.get('modified_date', '')
                
                logger.debug(f"🔍 Processing event: {title} (ID: {event_id}, Status: {status})")
                
                # Check if event is cancelled
                if status == 'cancelled':
                    if event_id in event_mappings:
                        # Event was previously synced but now cancelled - delete from all platforms
                        stored_info = event_mappings[event_id]
                        discord_event_id = stored_info.get('discord_id')
                        google_event_id = stored_info.get('google_id')
                        google_calendar_id = stored_info.get('google_calendar_id')
                        
                        # Delete from Discord
                        if discord_event_id:
                            self.delete_discord_event_direct(discord_event_id)
                            deleted_events_count += 1
                        
                        # Delete from Google Calendar
                        if google_event_id and google_calendar_id:
                            if self.delete_google_event(google_event_id, google_calendar_id):
                                deleted_events_count += 1
                        
                        del event_mappings[event_id]
                        logger.info(f"🗑️ Removed cancelled event: {title}")
                    else:
                        # Event is cancelled and was never synced - skip
                        logger.info(f"⏭️ Skipping cancelled event: {title}")
                    continue
                
                # Check if this is a new event or needs updating
                if event_id not in event_mappings:
                    # New event - create in Google Calendar and Discord
                    google_event_id, google_calendar_id = self.create_google_event(an_event)
                    
                    if google_event_id:
                        # Get the Google event data for Discord
                        google_event_data = self.transform_to_google_event(an_event)
                        
                        # Create Discord event
                        discord_event_id = self.create_discord_event_direct(an_event, google_event_data)
                        
                        event_mappings[event_id] = {
                            'discord_id': discord_event_id,
                            'google_id': google_event_id,
                            'google_calendar_id': google_calendar_id,
                            'last_modified': modified_date,
                            'status': status,
                            'title': title,
                            'action_network_url': an_event.get('browser_url', '')
                        }
                        new_events_count += 1
                        
                        # Log creation
                        hashtag_used, _ = self.get_google_calendar_id(an_event)
                        sync_status = []
                        if discord_event_id:
                            sync_status.append("Discord")
                        if google_event_id:
                            sync_status.append("Google Calendar")
                        
                        sync_status_str = " → " + " + ".join(sync_status) if sync_status else ""
                        logger.info(f"📅 NEW: '{title}' (ID: {event_id}) (hashtag: {hashtag_used}){sync_status_str}")
                    else:
                        logger.error(f"❌ Failed to create event: {title}")
                
                else:
                    # Existing event - check if it needs updating
                    stored_info = event_mappings[event_id]
                    
                    # Check if event has been modified
                    needs_update = False
                    update_reasons = []
                    
                    if modified_date != stored_info.get('last_modified', ''):
                        needs_update = True
                        update_reasons.append(f"modified_date changed from {stored_info.get('last_modified', 'unknown')} to {modified_date}")
                    
                    if status != stored_info.get('status', ''):
                        needs_update = True
                        update_reasons.append(f"status changed from {stored_info.get('status', 'unknown')} to {status}")
                    
                    if needs_update:
                        # Event has been modified - update in all platforms
                        logger.info(f"🔄 UPDATE DETECTED for '{title}': {', '.join(update_reasons)}")
                        
                        google_event_data = self.transform_to_google_event(an_event)
                        
                        if google_event_data:
                            updated_platforms = []
                            
                            # Update Google Calendar
                            if stored_info.get('google_id') and stored_info.get('google_calendar_id'):
                                google_result = self.update_google_event(
                                    stored_info['google_id'], 
                                    stored_info['google_calendar_id'], 
                                    an_event
                                )
                                if google_result:
                                    updated_platforms.append('Google Calendar')
                            
                            # Update Discord
                            if stored_info.get('discord_id'):
                                discord_result = self.update_discord_event_direct(
                                    stored_info['discord_id'], an_event, google_event_data
                                )
                                if discord_result:
                                    updated_platforms.append('Discord')
                            
                            if updated_platforms:
                                event_mappings[event_id]['last_modified'] = modified_date
                                event_mappings[event_id]['status'] = status
                                event_mappings[event_id]['title'] = title
                                updated_events_count += 1
                                logger.info(f"✅ UPDATED: '{title}' in {' + '.join(updated_platforms)}")
                            else:
                                logger.error(f"❌ Failed to update event: {title}")
                    else:
                        # No changes needed
                        logger.debug(f"✅ No changes needed for: {title}")
            
            logger.info(f"🔄 Sync complete. {new_events_count} new, {updated_events_count} updated, {deleted_events_count} deleted")
            return {
                'new_events': new_events_count,
                'updated_events': updated_events_count,
                'deleted_events': deleted_events_count
            }
            
        except Exception as e:
            logger.error(f"❌ Error during sync: {str(e)}")
            return {'error': str(e)}
    
    def test_action_network_connection(self):
        """
        Test connection to Action Network API
        """
        try:
            url = f"{self.action_network_base_url}/events"
            params = {'limit': 1}
            
            response = requests.get(url, headers=self.action_network_headers, params=params)
            
            if response.status_code == 200:
                logger.info("✅ Action Network API connection successful")
                return True
            else:
                logger.error(f"❌ Action Network API connection failed: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error testing Action Network connection: {str(e)}")
            return False
    
    def test_google_calendar_connection(self):
        """
        Test connection to Google Calendar API
        """
        if not self.google_service:
            return False
        
        try:
            # Try to list calendars to test the connection
            calendar_list = self.google_service.calendarList().list().execute()
            logger.info("✅ Google Calendar API connection successful")
            return True
        except Exception as e:
            logger.error(f"❌ Google Calendar API connection failed: {str(e)}")
            return False

# Initialize sync service
sync_service = ActionNetworkGoogleDiscordSync()

def background_sync():
    """
    Background thread for periodic syncing
    """
    while True:
        try:
            # Wait 30 minutes between syncs
            time.sleep(30 * 60)  # 30 minutes
            sync_service.sync_events()
        except Exception as e:
            logger.error(f"❌ Background sync error: {str(e)}")

# Start background services
if ACTION_NETWORK_API_KEY:
    sync_thread = threading.Thread(target=background_sync, daemon=True)
    sync_thread.start()
    logger.info("🔄 Background sync thread started (runs every 30 minutes)")

@app.route('/', methods=['GET'])
def home():
    """
    Home page - shows service status
    """
    discord_configured = bool(DISCORD_BOT_TOKEN and DISCORD_GUILD_ID)
    google_configured = bool(GOOGLE_SERVICE_ACCOUNT_JSON)
    
    return jsonify({
        'service': 'Action Network to Discord and Google Calendar Integration',
        'status': 'running',
        'mode': 'Dual Sync',
        'sync_interval': '30 minutes',
        'features': [
            'Creates events in Discord and Google Calendar',
            'Updates modified events in both platforms', 
            'Deletes cancelled events from both platforms',
            'Hashtag-based calendar routing for Google Calendar',
            'Registration links in descriptions',
            'Discord scheduled events'
        ],
        'platforms': {
            'action_network': 'source',
            'discord': 'events sync' if discord_configured else 'not configured',
            'google_calendar': 'calendar sync' if google_configured else 'not configured'
        },
        'hashtag_mapping': {
            'google_calendars': {
                '#dsa': 'Flint Hills Chapter DSA',
                '#action': 'Direct Action',
                '#education': 'Education',
                '#outreach': 'Outreach',
                '#social': 'Socials',
                '#steering': 'Steering Committee',
                '#volunteer': 'Volunteering and Mutual Aid'
            }
        },
        'endpoints': {
            'health': '/health',
            'sync_now': '/sync',
            'status': '/status',
            'mappings': '/mappings',
            'clear_mappings': '/clear-mappings',
            'debug': '/debug/action-network'
        }
    })

@app.route('/health', methods=['GET'])
def health_check():
    """
    Health check endpoint
    """
    action_network_configured = bool(ACTION_NETWORK_API_KEY)
    discord_configured = bool(DISCORD_BOT_TOKEN and DISCORD_GUILD_ID)
    google_configured = bool(GOOGLE_SERVICE_ACCOUNT_JSON)
    
    action_network_connection = False
    discord_connection = False
    google_connection = False
    
    if action_network_configured:
        action_network_connection = sync_service.test_action_network_connection()
    
    if discord_configured and DISCORD_BOT_TOKEN:
        # Test Discord API access with a simple API call
        try:
            headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
            response = requests.get(f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}", headers=headers)
            discord_connection = response.status_code == 200
        except:
            discord_connection = False
    
    if google_configured:
        google_connection = sync_service.test_google_calendar_connection()
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'config': {
            'action_network_api_configured': action_network_configured,
            'discord_bot_configured': discord_configured,
            'google_calendar_configured': google_configured,
            'action_network_connection_working': action_network_connection,
            'discord_connection_working': discord_connection,
            'google_calendar_connection_working': google_connection
        },
        'sync_status': {
            'total_mapped_events': len(event_mappings),
            'background_sync_running': True if ACTION_NETWORK_API_KEY else False,
            'platforms_syncing': [
                'Discord' if discord_connection else None,
                'Google Calendar' if google_connection else None
            ]
        }
    })

@app.route('/sync', methods=['POST'])
def manual_sync():
    """
    Manually trigger a sync
    """
    try:
        logger.info("🔄 Manual sync triggered")
        result = sync_service.sync_events()
        
        if 'error' in result:
            return jsonify({
                'status': 'error',
                'message': result['error']
            }), 500
        
        platforms = []
        if DISCORD_BOT_TOKEN and DISCORD_GUILD_ID:
            platforms.append('Discord')
        if GOOGLE_SERVICE_ACCOUNT_JSON:
            platforms.append('Google Calendar')
        
        return jsonify({
            'status': 'success',
            'message': f'Sync completed. {result["new_events"]} new, {result["updated_events"]} updated, {result["deleted_events"]} deleted.',
            'result': result,
            'total_mapped_events': len(event_mappings),
            'platforms_synced': platforms
        })
        
    except Exception as e:
        logger.error(f"❌ Manual sync error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/status', methods=['GET'])
def sync_status():
    """
    Get sync status
    """
    platforms = []
    if DISCORD_BOT_TOKEN and DISCORD_GUILD_ID:
        platforms.append('Discord')
    if GOOGLE_SERVICE_ACCOUNT_JSON:
        platforms.append('Google Calendar')
    
    return jsonify({
        'total_mapped_events': len(event_mappings),
        'last_sync': 'Running in background every 30 minutes',
        'next_sync': 'Within 30 minutes',
        'sync_mode': f'Dual platform (Action Network → {" + ".join(platforms)})',
        'platforms_configured': platforms
    })

@app.route('/force-update/<event_id>', methods=['POST'])
def force_update_event(event_id):
    """
    Force update a specific event from Action Network to all platforms
    """
    try:
        if event_id not in event_mappings:
            return jsonify({
                'status': 'error',
                'message': f'Event ID {event_id} not found in mappings'
            }), 404
        
        # Fetch the specific event from Action Network
        an_events = sync_service.fetch_action_network_events(limit=100)  # Get more events to find this one
        
        target_event = None
        for an_event in an_events:
            # Use same ID extraction logic as sync
            extracted_id = None
            identifiers = an_event.get('identifiers', [])
            
            if identifiers:
                for identifier in identifiers:
                    if isinstance(identifier, str) and ':' in identifier:
                        extracted_id = identifier.split(':')[-1]
                        break
                    elif isinstance(identifier, str):
                        extracted_id = identifier
                        break
            
            if not extracted_id:
                extracted_id = an_event.get('id', '')
            
            if not extracted_id:
                browser_url = an_event.get('browser_url', '')
                if browser_url:
                    extracted_id = browser_url.split('/')[-1]
            
            if extracted_id == event_id:
                target_event = an_event
                break
        
        if not target_event:
            return jsonify({
                'status': 'error',
                'message': f'Event ID {event_id} not found in Action Network'
            }), 404
        
        # Transform and update the event
        google_event_data = sync_service.transform_to_google_event(target_event)
        
        if google_event_data:
            stored_info = event_mappings[event_id]
            updated_platforms = []
            
            # Update Google Calendar
            if stored_info.get('google_id') and stored_info.get('google_calendar_id'):
                google_result = sync_service.update_google_event(
                    stored_info['google_id'], 
                    stored_info['google_calendar_id'], 
                    target_event
                )
                if google_result:
                    updated_platforms.append('Google Calendar')
            
            # Update Discord
            if stored_info.get('discord_id'):
                discord_result = sync_service.update_discord_event_direct(
                    stored_info['discord_id'], target_event, google_event_data
                )
                if discord_result:
                    updated_platforms.append('Discord')
            
            if updated_platforms:
                # Update stored info
                event_mappings[event_id]['last_modified'] = target_event.get('modified_date', '')
                event_mappings[event_id]['status'] = target_event.get('status', 'confirmed')
                event_mappings[event_id]['title'] = target_event.get('title', 'No title')
                
                logger.info(f"🔄 Force updated event: {target_event.get('title', 'No title')}")
                
                return jsonify({
                    'status': 'success',
                    'message': f'Successfully force updated event: {target_event.get("title", "No title")}',
                    'discord_event_id': stored_info.get('discord_id'),
                    'google_event_id': stored_info.get('google_id'),
                    'platforms_updated': updated_platforms
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to update event in any platform'
                }), 500
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to transform event data'
            }), 500
            
    except Exception as e:
        logger.error(f"❌ Force update error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/clear-mappings', methods=['POST'])
def clear_mappings():
    """
    Clear all event mappings (use if you have duplicates and want to start fresh)
    """
    global event_mappings
    count = len(event_mappings)
    event_mappings.clear()
    
    logger.info(f"🗑️ Cleared {count} event mappings")
    
    return jsonify({
        'status': 'success',
        'message': f'Cleared {count} event mappings. Next sync will treat all events as new.',
        'warning': 'This may create duplicates if events already exist in Discord and Google Calendar. Consider manually cleaning both platforms first.'
    })

@app.route('/debug/mappings', methods=['GET'])
def debug_mappings():
    """
    Debug endpoint to see detailed mapping information
    """
    detailed_mappings = {}
    
    for an_id, mapping in event_mappings.items():
        detailed_mappings[an_id] = {
            'discord_id': mapping.get('discord_id'),
            'google_id': mapping.get('google_id'),
            'google_calendar_id': mapping.get('google_calendar_id'),
            'last_modified': mapping['last_modified'],
            'status': mapping['status'],
            'title': mapping.get('title', 'Unknown'),
            'action_network_url': mapping.get('action_network_url', '')
        }
    
    return jsonify({
        'total_mappings': len(event_mappings),
        'detailed_mappings': detailed_mappings
    })

@app.route('/mappings', methods=['GET'])
def event_mappings_status():
    """
    Show current event mappings
    """
    return jsonify({
        'total_events': len(event_mappings),
        'mappings': event_mappings
    })

@app.route('/debug/action-network', methods=['GET'])
def debug_action_network():
    """
    Debug endpoint to see raw Action Network response
    """
    results = {}
    
    # Test 1: Basic connection without filters
    try:
        url = f"{sync_service.action_network_base_url}/events"
        response = requests.get(url, headers=sync_service.action_network_headers)
        
        results['test1_no_filter'] = {
            'status_code': response.status_code,
            'response': response.json() if response.status_code == 200 else response.text[:500]
        }
    except Exception as e:
        results['test1_no_filter'] = {'error': str(e)}
    
    # Test 2: Check API endpoint
    try:
        url = "https://actionnetwork.org/api/v2/"
        response = requests.get(url, headers=sync_service.action_network_headers)
        
        results['test2_api_root'] = {
            'status_code': response.status_code,
            'response': response.json() if response.status_code == 200 else response.text[:500]
        }
    except Exception as e:
        results['test2_api_root'] = {'error': str(e)}
    
    # Test 3: Check headers
    results['test3_headers'] = {
        'headers_used': dict(sync_service.action_network_headers),
        'api_key_configured': bool(ACTION_NETWORK_API_KEY),
        'api_key_length': len(ACTION_NETWORK_API_KEY) if ACTION_NETWORK_API_KEY else 0
    }
    
    return jsonify(results)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    
    # Check configuration
    if not ACTION_NETWORK_API_KEY:
        logger.warning("⚠️  Action Network API key not configured!")
    
    if not DISCORD_BOT_TOKEN:
        logger.warning("⚠️  Discord bot token not configured!")
    
    if not DISCORD_GUILD_ID:
        logger.warning("⚠️  Discord guild ID not configured!")
    
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        logger.warning("⚠️  Google Calendar service account not configured!")
    
    logger.info(f"🚀 Starting Action Network Dual-Platform Sync Service on port {port}")
    logger.info(f"📡 Manual sync endpoint: /sync")
    logger.info(f"❤️  Health check: /health")
    logger.info(f"📊 Status endpoint: /status")
    logger.info(f"🔗 Mappings endpoint: /mappings")
    logger.info(f"🎮 Discord integration: {'enabled' if DISCORD_BOT_TOKEN and DISCORD_GUILD_ID else 'disabled'}")
    logger.info(f"📅 Google Calendar integration: {'enabled' if GOOGLE_SERVICE_ACCOUNT_JSON else 'disabled'}")
    logger.info(f"🐛 Debug endpoint: /debug/action-network")
    
    app.run(host='0.0.0.0', port=port, debug=False)
