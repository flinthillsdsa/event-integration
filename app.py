#!/usr/bin/env python3
"""
Action Network to TeamUp and Discord Integration Service
Full three-way sync: Action Network → TeamUp → Discord Events
Version 4.1 - Discord REST API Integration
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


def clean_description_for_display(description):
    """Remove hashtags from description for display purposes"""
    if not description:
        return description
    
    # Remove hashtags (# followed by word characters)
    cleaned = re.sub(r'#\w+', '', description)
    
    # Clean up extra whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    return cleaned

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration - set these as environment variables
TEAMUP_API_KEY = os.environ.get('TEAMUP_API_KEY')
TEAMUP_CALENDAR_KEY = os.environ.get('TEAMUP_CALENDAR_KEY')
ACTION_NETWORK_API_KEY = os.environ.get('ACTION_NETWORK_API_KEY')
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN')
DISCORD_GUILD_ID = int(os.environ.get('DISCORD_GUILD_ID', 0)) if os.environ.get('DISCORD_GUILD_ID') else None
ACTION_NETWORK_ORG = 'fhdsa'  # Your organization slug

# In-memory storage for event mappings (in production, you'd use a database)
# Format: {action_network_id: {'teamup_id': '123', 'discord_id': '456', 'last_modified': '2025-06-22T...', 'status': 'confirmed'}}
event_mappings = {}

class ActionNetworkTeamUpDiscordSync:
    def __init__(self):
        self.teamup_base_url = f"https://api.teamup.com/{TEAMUP_CALENDAR_KEY}"
        self.teamup_headers = {
            'Teamup-Token': TEAMUP_API_KEY,
            'Content-Type': 'application/json'
        }
        self.action_network_headers = {
            'OSDI-API-Token': ACTION_NETWORK_API_KEY,
            'Content-Type': 'application/json'
        }
        self.action_network_base_url = 'https://actionnetwork.org/api/v2'
    
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
    
    def get_subcalendar_id(self, an_event):
        """
        Determine which TeamUp subcalendar to use based on hashtags anywhere in Action Network event description
        Hashtags can be at the beginning, middle, or end of the description.
        """
        description = an_event.get('description', '').lower()
        
        # Hashtag to subcalendar mapping
        hashtag_mapping = {
         
            '#meetings': 14502151,    # Meetings
            '#outreach': 14816011,   # Outreach/Canvassing/Tabling
            '#education': 14815998,  # Political Education
            '#social': 14816002,     # Socials
            '#civic': 14816009   # CIVIC
        }
        
        # Check for hashtags anywhere in description
        for hashtag, subcalendar_id in hashtag_mapping.items():
            if hashtag in description:
                return subcalendar_id
        
        # Default to General Membership if no hashtag found
        return 14502151  # General Membership
    
    def transform_action_network_event(self, an_event):
        """
        Transform Action Network event to TeamUp format
        """
        try:
            title = an_event.get('title', 'Untitled Event')
            original_description = an_event.get('description', '')
            description = clean_description_for_display(original_description)
            registration_url = an_event.get('browser_url', '')
            
            # Create enhanced description with registration link
            enhanced_description = description
            if registration_url:
                if description:
                    # Use HTML link format
                    enhanced_description += f'\n\n<a href="{registration_url}" target="_blank">RSVP</a>'
                else:
                    enhanced_description = f'<a href="{registration_url}" target="_blank">RSVPe</a>'
            
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
            
            # Determine which subcalendar to use
            subcalendar_id = self.get_subcalendar_id(an_event)
            
            # TeamUp event format
            teamup_event = {
                'subcalendar_ids': [subcalendar_id],
                'title': title,
                'notes': enhanced_description,
                'location': location_str,
                'start_dt': start_date,
                'end_dt': end_date,
                'all_day': False
            }
            
            # Remove empty fields
            teamup_event = {k: v for k, v in teamup_event.items() if v}
            
            return teamup_event
            
        except Exception as e:
            logger.error(f"❌ Error transforming event: {str(e)}")
            return None
    
    def create_teamup_event(self, event_data):
        """
        Create an event in TeamUp Calendar
        """
        try:
            url = f"{self.teamup_base_url}/events"
            
            response = requests.post(url, headers=self.teamup_headers, json=event_data)
            
            if response.status_code == 201:
                result = response.json()
                logger.info(f"✅ Created TeamUp event: {event_data.get('title', 'No title')}")
                return result
            else:
                logger.error(f"❌ Failed to create TeamUp event: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error creating TeamUp event: {str(e)}")
            return None
    
    def update_teamup_event(self, teamup_event_id, event_data):
        """
        Update an existing event in TeamUp Calendar
        """
        try:
            url = f"{self.teamup_base_url}/events/{teamup_event_id}"
            
            response = requests.put(url, headers=self.teamup_headers, json=event_data)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"✅ Updated TeamUp event: {event_data.get('title', 'No title')}")
                return result
            else:
                logger.error(f"❌ Failed to update TeamUp event: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error updating TeamUp event: {str(e)}")
            return None
    
    def delete_teamup_event(self, teamup_event_id):
        """
        Delete an event from TeamUp Calendar
        """
        try:
            url = f"{self.teamup_base_url}/events/{teamup_event_id}"
            
            response = requests.delete(url, headers=self.teamup_headers)
            
            if response.status_code == 204:
                logger.info(f"✅ Deleted TeamUp event ID: {teamup_event_id}")
                return True
            else:
                logger.error(f"❌ Failed to delete TeamUp event: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error deleting TeamUp event: {str(e)}")
            return False
    
    def create_discord_event_direct(self, an_event, teamup_event_data):
        """
        Create a Discord scheduled event using direct REST API calls (no asyncio)
        """
        if not DISCORD_BOT_TOKEN or not DISCORD_GUILD_ID:
            logger.warning("⚠️ Discord bot not configured, skipping Discord event creation")
            return None
        
        try:
            # Convert times to ISO format
            start_time = teamup_event_data['start_dt']
            end_time = teamup_event_data.get('end_dt', start_time)
            
            # Create Discord event description (Discord doesn't support HTML)
            original_description = an_event.get('description', '')
            description = clean_description_for_display(original_description)
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
                "name": teamup_event_data['title'],
                "description": description,
                "scheduled_start_time": start_time,
                "scheduled_end_time": end_time,
                "privacy_level": 2,  # GUILD_ONLY
                "entity_type": 3,    # EXTERNAL
                "entity_metadata": {
                    "location": teamup_event_data.get('location', 'TBD')
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
                logger.info(f"🎮 Created Discord event: {teamup_event_data['title']} (ID: {discord_event_id})")
                return discord_event_id
            else:
                logger.error(f"❌ Failed to create Discord event: {response.status_code} - {response.text}")
                return None
            
        except Exception as e:
            logger.error(f"❌ Error creating Discord event: {str(e)}")
            return None
    
    def update_discord_event_direct(self, discord_event_id, an_event, teamup_event_data):
        """
        Update a Discord scheduled event using direct REST API calls
        """
        if not DISCORD_BOT_TOKEN or not DISCORD_GUILD_ID:
            return None
        
        try:
            # Convert times to ISO format
            start_time = teamup_event_data['start_dt']
            end_time = teamup_event_data.get('end_dt', start_time)
            
            # Create Discord event description (Discord doesn't support HTML)
            original_description = an_event.get('description', '')
            description = clean_description_for_display(original_description)
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
                "name": teamup_event_data['title'],
                "description": description,
                "scheduled_start_time": start_time,
                "scheduled_end_time": end_time,
                "entity_metadata": {
                    "location": teamup_event_data.get('location', 'TBD')
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
                logger.info(f"🎮 Updated Discord event: {teamup_event_data['title']}")
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
        Full three-way sync of events: Action Network → TeamUp → Discord
        """
        try:
            logger.info("🔄 Starting full event sync (Action Network → TeamUp → Discord)...")
            
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
                        # Event was previously synced but now cancelled - delete from TeamUp and Discord
                        stored_info = event_mappings[event_id]
                        teamup_event_id = stored_info['teamup_id']
                        discord_event_id = stored_info.get('discord_id')
                        
                        # Delete from TeamUp
                        if self.delete_teamup_event(teamup_event_id):
                            deleted_events_count += 1
                        
                        # Delete from Discord using direct API
                        if discord_event_id:
                            self.delete_discord_event_direct(discord_event_id)
                        
                        del event_mappings[event_id]
                        logger.info(f"🗑️ Removed cancelled event: {title}")
                    else:
                        # Event is cancelled and was never synced - skip
                        logger.info(f"⏭️ Skipping cancelled event: {title}")
                    continue
                
                # Check if this is a new event or needs updating
                if event_id not in event_mappings:
                    # New event - create in TeamUp and Discord
                    teamup_event_data = self.transform_action_network_event(an_event)
                    
                    if teamup_event_data:
                        result = self.create_teamup_event(teamup_event_data)
                        
                        if result:
                            teamup_event_id = result.get('event', {}).get('id')
                            
                            # Create Discord event using direct API
                            discord_event_id = self.create_discord_event_direct(an_event, teamup_event_data)
                            
                            event_mappings[event_id] = {
                                'teamup_id': teamup_event_id,
                                'discord_id': discord_event_id,
                                'last_modified': modified_date,
                                'status': status,
                                'title': title,
                                'action_network_url': an_event.get('browser_url', '')
                            }
                            new_events_count += 1
                            
                            # Log subcalendar assignment
                            subcalendar_names = {
                                14502151: "Meetings",
                                14815998: "Political Education",
                                14816011: "Outreach/Canvassing/Tabling",
                                14816002: "Socials",
                                14816009: "Community Involvement and Community Initiatives"
                            }
                            
                            hashtag_used = "none (defaulted to Meetings)"
                            description_lower = an_event.get('description', '').lower()
                            hashtags = ['#meetings', '#education', '#outreach',  '#social', '#civic']
                            for hashtag in hashtags:
                                if hashtag in description_lower:
                                    hashtag_used = hashtag
                                    break
                            
                            subcalendar_id = teamup_event_data.get('subcalendar_ids', [14502151])[0]
                            discord_status = "🎮 + Discord" if discord_event_id else ""
                            logger.info(f"📅 NEW: '{title}' (ID: {event_id}) → {subcalendar_names.get(subcalendar_id, 'Unknown')} (hashtag: {hashtag_used}) {discord_status}")
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
                        # Event has been modified - update in TeamUp and Discord
                        logger.info(f"🔄 UPDATE DETECTED for '{title}': {', '.join(update_reasons)}")
                        
                        teamup_event_data = self.transform_action_network_event(an_event)
                        
                        if teamup_event_data:
                            # Update TeamUp
                            result = self.update_teamup_event(stored_info['teamup_id'], teamup_event_data)
                            
                            if result:
                                # Update Discord using direct API
                                if stored_info.get('discord_id'):
                                    self.update_discord_event_direct(stored_info['discord_id'], an_event, teamup_event_data)
                                
                                event_mappings[event_id]['last_modified'] = modified_date
                                event_mappings[event_id]['status'] = status
                                event_mappings[event_id]['title'] = title
                                updated_events_count += 1
                                logger.info(f"✅ UPDATED: '{title}' in TeamUp and Discord")
                            else:
                                logger.error(f"❌ Failed to update TeamUp event: {title}")
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
    
    def test_teamup_connection(self):
        """
        Test connection to TeamUp API
        """
        try:
            url = f"{self.teamup_base_url}/events"
            response = requests.get(url, headers=self.teamup_headers, params={'limit': 1})
            
            if response.status_code == 200:
                logger.info("✅ TeamUp API connection successful")
                return True
            else:
                logger.error(f"❌ TeamUp API connection failed: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error testing TeamUp connection: {str(e)}")
            return False

# Initialize sync service
sync_service = ActionNetworkTeamUpDiscordSync()

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
    
    return jsonify({
        'service': 'Action Network to TeamUp and Discord Integration',
        'status': 'running',
        'mode': 'Three-Way Sync',
        'sync_interval': '30 minutes',
        'features': [
            'Creates events in TeamUp and Discord',
            'Updates modified events in both platforms', 
            'Deletes cancelled events from both platforms',
            'Hashtag-based subcalendar mapping',
            'Registration links in descriptions',
            'Discord scheduled events'
        ],
        'platforms': {
            'action_network': 'source',
            'teamup': 'calendar sync',
            'discord': 'events sync' if discord_configured else 'not configured'
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
    teamup_configured = bool(TEAMUP_API_KEY and TEAMUP_CALENDAR_KEY)
    action_network_configured = bool(ACTION_NETWORK_API_KEY)
    discord_configured = bool(DISCORD_BOT_TOKEN and DISCORD_GUILD_ID)
    
    teamup_connection = False
    action_network_connection = False
    discord_connection = False
    
    if teamup_configured:
        teamup_connection = sync_service.test_teamup_connection()
    
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
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'config': {
            'teamup_api_configured': teamup_configured,
            'action_network_api_configured': action_network_configured,
            'discord_bot_configured': discord_configured,
            'teamup_connection_working': teamup_connection,
            'action_network_connection_working': action_network_connection,
            'discord_connection_working': discord_connection
        },
        'sync_status': {
            'total_mapped_events': len(event_mappings),
            'background_sync_running': True if ACTION_NETWORK_API_KEY else False,
            'discord_bot_running': discord_connection
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
        
        return jsonify({
            'status': 'success',
            'message': f'Sync completed. {result["new_events"]} new, {result["updated_events"]} updated, {result["deleted_events"]} deleted.',
            'result': result,
            'total_mapped_events': len(event_mappings),
            'platforms_synced': ['TeamUp', 'Discord'] if DISCORD_BOT_TOKEN and DISCORD_GUILD_ID else ['TeamUp']
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
    return jsonify({
        'total_mapped_events': len(event_mappings),
        'last_sync': 'Running in background every 30 minutes',
        'next_sync': 'Within 30 minutes',
        'sync_mode': 'Three-way (Action Network → TeamUp → Discord)',
        'discord_status': 'configured' if DISCORD_BOT_TOKEN and DISCORD_GUILD_ID else 'not configured'
    })

@app.route('/force-update/<event_id>', methods=['POST'])
def force_update_event(event_id):
    """
    Force update a specific event from Action Network to TeamUp and Discord
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
        teamup_event_data = sync_service.transform_action_network_event(target_event)
        
        if teamup_event_data:
            stored_info = event_mappings[event_id]
            
            # Update TeamUp
            result = sync_service.update_teamup_event(stored_info['teamup_id'], teamup_event_data)
            
            # Update Discord using direct API
            discord_updated = False
            if stored_info.get('discord_id'):
                discord_result = sync_service.update_discord_event_direct(
                    stored_info['discord_id'], target_event, teamup_event_data
                )
                discord_updated = discord_result is not None
            
            if result:
                # Update stored info
                event_mappings[event_id]['last_modified'] = target_event.get('modified_date', '')
                event_mappings[event_id]['status'] = target_event.get('status', 'confirmed')
                event_mappings[event_id]['title'] = target_event.get('title', 'No title')
                
                logger.info(f"🔄 Force updated event: {target_event.get('title', 'No title')}")
                
                return jsonify({
                    'status': 'success',
                    'message': f'Successfully force updated event: {target_event.get("title", "No title")}',
                    'teamup_event_id': stored_info['teamup_id'],
                    'discord_event_id': stored_info.get('discord_id'),
                    'platforms_updated': ['TeamUp'] + (['Discord'] if discord_updated else [])
                })
            else:
                return jsonify({
                    'status': 'error',
                    'message': 'Failed to update event in TeamUp'
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
        'warning': 'This may create duplicates if events already exist in TeamUp and Discord. Consider manually cleaning both platforms first.'
    })

@app.route('/debug/mappings', methods=['GET'])
def debug_mappings():
    """
    Debug endpoint to see detailed mapping information
    """
    detailed_mappings = {}
    
    for an_id, mapping in event_mappings.items():
        detailed_mappings[an_id] = {
            'teamup_id': mapping['teamup_id'],
            'discord_id': mapping.get('discord_id'),
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
    if not TEAMUP_API_KEY:
        logger.warning("⚠️  TeamUp API key not configured!")
    
    if not TEAMUP_CALENDAR_KEY:
        logger.warning("⚠️  TeamUp Calendar key not configured!")
    
    if not ACTION_NETWORK_API_KEY:
        logger.warning("⚠️  Action Network API key not configured!")
    
    if not DISCORD_BOT_TOKEN:
        logger.warning("⚠️  Discord bot token not configured!")
    
    if not DISCORD_GUILD_ID:
        logger.warning("⚠️  Discord guild ID not configured!")
    
    logger.info(f"🚀 Starting Action Network to TeamUp and Discord Sync Service on port {port}")
    logger.info(f"📡 Manual sync endpoint: /sync")
    logger.info(f"❤️  Health check: /health")
    logger.info(f"📊 Status endpoint: /status")
    logger.info(f"🔗 Mappings endpoint: /mappings")
    logger.info(f"🎮 Discord integration: {'enabled' if DISCORD_BOT_TOKEN and DISCORD_GUILD_ID else 'disabled'}")
    logger.info(f"🐛 Debug endpoint: /debug/action-network")
    
    app.run(host='0.0.0.0', port=port, debug=False)
