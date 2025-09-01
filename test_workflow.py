#!/usr/bin/env python3
"""
Quick test to verify the improved workflow layout.
"""

import requests

BASE_URL = "https://mtgprep-executive-brief.onrender.com"

def test_workflow_layout():
    """Test that the workflow sections appear in the correct order."""
    print("🔄 Testing Improved Workflow Layout")
    print("=" * 40)
    
    try:
        response = requests.get(f"{BASE_URL}/bd")
        
        if response.status_code == 200:
            content = response.text
            
            # Find the positions of key sections
            attendees_pos = content.find("Meeting Attendees")
            phase1_pos = content.find("Phase 1: Research Attendees")
            meeting_context_pos = content.find("Meeting Context & Objectives")
            phase2_pos = content.find("Phase 2: Generate Intelligence Report")
            
            print("📍 Section Positions:")
            print(f"  1. Meeting Attendees: {attendees_pos}")
            print(f"  2. Phase 1 Research: {phase1_pos}")
            print(f"  3. Meeting Context: {meeting_context_pos}")
            print(f"  4. Phase 2 Intelligence: {phase2_pos}")
            
            # Check correct order
            if (attendees_pos < phase1_pos < meeting_context_pos < phase2_pos):
                print("\n✅ Perfect workflow order!")
                print("   📝 Add Attendees")
                print("   🔍 Research Attendees (Phase 1)")
                print("   📋 Define Meeting Context")
                print("   🧠 Generate Intelligence Report (Phase 2)")
                return True
            else:
                print("\n❌ Workflow order incorrect")
                return False
                
        else:
            print(f"❌ Failed to load page: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ Test failed: {str(e)}")
        return False

if __name__ == "__main__":
    print("🚀 Testing Improved BD Workflow")
    print("=" * 50)
    
    success = test_workflow_layout()
    
    if success:
        print("\n🎉 Workflow improvement successfully deployed!")
        print("Users now follow a logical progression:")
        print("1️⃣  Add attendees")
        print("2️⃣  Research them (Phase 1)")
        print("3️⃣  Define meeting context")
        print("4️⃣  Generate intelligence report (Phase 2)")
    else:
        print("\n❌ Workflow layout needs attention")
    
    exit(0 if success else 1)
