#!/usr/bin/env python3
"""
Debug HubSpot search to understand why Peter Secor isn't being found.
"""

import requests
import json
import os

BASE_URL = "https://mtgprep-executive-brief.onrender.com"
HUBSPOT_TOKEN = os.getenv("HUBSPOT_TOKEN") or os.getenv("HUBSPOT_PRIVATE_APP_TOKEN")

def debug_peter_secor_hubspot():
    """Debug why Peter Secor isn't found in HubSpot search."""
    print("🔍 Debugging Peter Secor HubSpot Search")
    print("=" * 45)
    
    if not HUBSPOT_TOKEN:
        print("❌ HubSpot token not found in environment")
        return False
    
    # Direct HubSpot API call to get Peter's record
    contact_id = "151944029221"
    
    try:
        print(f"📞 Fetching Peter Secor's record directly (ID: {contact_id})...")
        
        headers = {
            "Authorization": f"Bearer {HUBSPOT_TOKEN}",
            "Content-Type": "application/json"
        }
        
        # Get the specific contact
        response = requests.get(
            f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}",
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            contact_data = response.json()
            properties = contact_data.get("properties", {})
            
            print("✅ Found Peter Secor's HubSpot record!")
            print("📋 Stored Properties:")
            print(f"  • First Name: '{properties.get('firstname', 'N/A')}'")
            print(f"  • Last Name: '{properties.get('lastname', 'N/A')}'")
            print(f"  • Email: '{properties.get('email', 'N/A')}'")
            print(f"  • Company: '{properties.get('company', 'N/A')}'")
            print(f"  • Job Title: '{properties.get('jobtitle', 'N/A')}'")
            print(f"  • LinkedIn URL: '{properties.get('linkedin_url', 'N/A')}'")
            
            # Now test our search logic
            print(f"\n🔍 Testing Our Search Logic:")
            
            # Test 1: Search by exact stored name
            stored_first = properties.get('firstname', '')
            stored_last = properties.get('lastname', '')
            stored_company = properties.get('company', '')
            
            if stored_first and stored_last:
                full_name = f"{stored_first} {stored_last}"
                print(f"  • Stored name: '{full_name}'")
                print(f"  • Stored company: '{stored_company}'")
                
                # Test our app's search
                test_payload = {
                    "attendees": [
                        {
                            "name": full_name,
                            "title": "",
                            "company": stored_company,
                            "email": properties.get('email', '')
                        }
                    ],
                    "target_company": stored_company,
                    "check_hubspot": True
                }
                
                print(f"\n🧪 Testing app search with exact stored values...")
                
                app_response = requests.post(
                    f"{BASE_URL}/api/bd/research-attendees",
                    json=test_payload,
                    timeout=60
                )
                
                if app_response.status_code == 200:
                    app_data = app_response.json()
                    researched = app_data.get('researched_attendees', [])
                    
                    if researched and researched[0].get('hubspot_contact'):
                        found_id = researched[0]['hubspot_contact'].get('_id') or researched[0]['hubspot_contact'].get('id')
                        print(f"✅ App found contact! ID: {found_id}")
                        if str(found_id) == contact_id:
                            print("🎉 PERFECT MATCH! Search is working correctly.")
                            return True
                        else:
                            print(f"⚠️  Found different contact: {found_id} vs expected {contact_id}")
                            return False
                    else:
                        print("❌ App still didn't find the contact")
                        print("🔧 This suggests our search logic needs more work")
                        return False
                else:
                    print(f"❌ App search failed: {app_response.status_code}")
                    return False
            else:
                print("❌ Contact missing firstname/lastname in HubSpot")
                return False
                
        else:
            print(f"❌ Failed to fetch contact: {response.status_code}")
            print(f"Response: {response.text[:200]}...")
            return False
            
    except Exception as e:
        print(f"❌ Debug failed: {str(e)}")
        return False

if __name__ == "__main__":
    success = debug_peter_secor_hubspot()
    
    if success:
        print("\n🎉 HubSpot search debugging successful!")
        print("Peter Secor should now be found correctly.")
    else:
        print("\n🔧 HubSpot search needs further investigation.")
        print("Check the debug output above for clues.")
    
    exit(0 if success else 1)
