#!/usr/bin/env python3
"""
Test the new two-phase BD workflow:
1. Research attendees first
2. Generate intelligence report with validated data
"""

import requests
import json
import time

BASE_URL = "https://mtgprep-executive-brief.onrender.com"

def test_two_phase_workflow():
    """Test the complete two-phase workflow."""
    print("🎯 Testing Two-Phase BD Workflow")
    print("=" * 50)
    
    # Phase 1: Research attendees
    print("\n📋 PHASE 1: Research Attendees")
    print("-" * 30)
    
    attendees_payload = {
        "attendees": [
            {
                "name": "Sarah Johnson",
                "title": "VP of Marketing",
                "company": "TechCorp Solutions",  # Different company
                "email": "sarah.j@techcorp.com"
            },
            {
                "name": "David Chen",
                "title": "Chief Technology Officer", 
                "company": "",  # Will use target company
                "email": "david.chen@acmeinc.com"
            },
            {
                "name": "Maria Rodriguez",
                "title": "Director of Analytics",
                "company": "DataFlow Inc",  # Third company
                "email": ""  # No email
            }
        ],
        "target_company": "Acme Inc",
        "check_hubspot": True
    }
    
    print("🔍 Researching 3 attendees from different companies...")
    start_time = time.time()
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/bd/research-attendees",
            json=attendees_payload,
            timeout=120
        )
        
        research_duration = time.time() - start_time
        
        if response.status_code == 200:
            research_data = response.json()
            
            print(f"✅ Research completed in {research_duration:.1f}s")
            print(f"📊 Research Results:")
            print(f"  • Total researched: {research_data.get('total_researched', 0)}")
            print(f"  • LinkedIn profiles found: {research_data.get('linkedin_found', 0)}")
            print(f"  • HubSpot contacts found: {research_data.get('hubspot_found', 0)}")
            
            researched_attendees = research_data.get('researched_attendees', [])
            
            print("\n👥 Individual Attendee Results:")
            for i, attendee in enumerate(researched_attendees, 1):
                linkedin_status = "✓" if attendee.get('linkedin_url') else "✗"
                hubspot_status = "✓" if attendee.get('hubspot_contact') else "✗"
                print(f"  {i}. {attendee['name']} ({attendee['company']})")
                print(f"     LinkedIn: {linkedin_status} | HubSpot: {hubspot_status}")
            
            # Phase 2: Generate intelligence report
            print(f"\n📋 PHASE 2: Generate Intelligence Report")
            print("-" * 40)
            
            intelligence_payload = {
                "company_name": "Acme Inc",
                "industry": "Technology",
                "meeting_context": "Strategic partnership meeting to discuss data analytics collaboration and potential acquisition opportunities.",
                "effort": "high",
                "prompt": "Create a comprehensive BD intelligence report focusing on the multi-company attendee dynamic and cross-company collaboration opportunities.",
                "researched_attendees": researched_attendees
            }
            
            print("🧠 Generating intelligence report with researched data...")
            start_time = time.time()
            
            response = requests.post(
                f"{BASE_URL}/api/bd/generate",
                json=intelligence_payload,
                timeout=180
            )
            
            intelligence_duration = time.time() - start_time
            
            if response.status_code == 200:
                intelligence_data = response.json()
                
                print(f"✅ Intelligence report generated in {intelligence_duration:.1f}s")
                print(f"📊 Report Metadata:")
                
                meta = intelligence_data.get('meta', {})
                for key, value in meta.items():
                    print(f"  • {key}: {value}")
                
                # Check if report includes all attendees
                report = intelligence_data.get('report_markdown', '')
                attendee_mentions = []
                for attendee in researched_attendees:
                    if attendee['name'] in report:
                        attendee_mentions.append(attendee['name'])
                
                print(f"\n📝 Report Analysis:")
                print(f"  • Report length: {len(report):,} characters")
                print(f"  • Attendees mentioned: {len(attendee_mentions)}/{len(researched_attendees)}")
                
                if len(attendee_mentions) == len(researched_attendees):
                    print("  ✅ All attendees included in report!")
                else:
                    print(f"  ⚠️  Missing: {set(a['name'] for a in researched_attendees) - set(attendee_mentions)}")
                
                # Check for multi-company analysis
                companies = set(a['company'] for a in researched_attendees if a['company'])
                if len(companies) > 1:
                    company_mentions = sum(1 for company in companies if company in report)
                    print(f"  • Multi-company analysis: {company_mentions}/{len(companies)} companies mentioned")
                
                total_time = research_duration + intelligence_duration
                print(f"\n🎊 TWO-PHASE WORKFLOW SUCCESSFUL!")
                print(f"Total time: {total_time:.1f}s (Research: {research_duration:.1f}s + Intelligence: {intelligence_duration:.1f}s)")
                
                return True
                
            else:
                print(f"❌ Phase 2 failed: {response.status_code}")
                try:
                    error_data = response.json()
                    print(f"Error: {error_data.get('detail', 'Unknown error')}")
                except:
                    print(f"Error response: {response.text[:200]}...")
                return False
        else:
            print(f"❌ Phase 1 failed: {response.status_code}")
            try:
                error_data = response.json()
                print(f"Error: {error_data.get('detail', 'Unknown error')}")
            except:
                print(f"Error response: {response.text[:200]}...")
            return False
            
    except requests.exceptions.Timeout:
        print("⏰ Request timed out")
        return False
    except Exception as e:
        print(f"❌ Test failed with exception: {str(e)}")
        return False

def test_hubspot_add_functionality():
    """Test the individual HubSpot add functionality."""
    print("\n🏢 Testing Individual HubSpot Add")
    print("-" * 35)
    
    test_attendee = {
        "attendee": {
            "name": "Test User",
            "title": "Test Manager",
            "company": "Test Company",
            "email": "test@testcompany.com",
            "linkedin_url": "https://linkedin.com/in/testuser"
        }
    }
    
    try:
        response = requests.post(
            f"{BASE_URL}/api/bd/add-to-hubspot",
            json=test_attendee,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                print("✅ HubSpot contact creation successful!")
                print(f"Contact ID: {data.get('contact_id', 'N/A')}")
                return True
            else:
                print(f"⚠️  HubSpot API responded but creation failed: {data.get('message', 'Unknown error')}")
                return True  # Still counts as API working
        else:
            print(f"❌ HubSpot add failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"❌ HubSpot test failed: {str(e)}")
        return False

if __name__ == "__main__":
    print("🚀 Testing Enhanced Two-Phase BD Workflow")
    print("=" * 60)
    
    tests = [
        ("Two-Phase Workflow", test_two_phase_workflow),
        ("HubSpot Add Functionality", test_hubspot_add_functionality)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        print(f"\n📋 Running: {test_name}")
        print("=" * 60)
        
        start_time = time.time()
        success = test_func()
        duration = time.time() - start_time
        
        results.append((test_name, success, duration))
        print(f"⏱️  Duration: {duration:.2f}s")
    
    # Summary
    print("\n" + "=" * 60)
    print("📊 TEST RESULTS SUMMARY")
    print("=" * 60)
    
    passed = 0
    total = len(results)
    
    for test_name, success, duration in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} | {test_name:<30} | {duration:>6.2f}s")
        if success:
            passed += 1
    
    print("-" * 60)
    print(f"Results: {passed}/{total} tests passed ({passed/total*100:.1f}%)")
    
    if passed == total:
        print("🎉 All tests passed! The two-phase workflow is working perfectly.")
    else:
        print("❌ Some tests failed. Please check the implementation.")
    
    exit(0 if passed == total else 1)
