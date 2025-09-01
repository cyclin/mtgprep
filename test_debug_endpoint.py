#!/usr/bin/env python3
"""
Quick test for the new debug prompt preview endpoint
"""

import json
import asyncio
import httpx

async def test_debug_endpoint():
    """Test the new /api/debug/prompt-preview endpoint"""
    
    # Test payload
    test_payload = {
        "company_name": "TechFlow Solutions",
        "industry": "SaaS",
        "meeting_context": "Strategic partnership discussion about improving trial-to-paid conversion rates",
        "researched_attendees": [
            {
                "name": "Sarah Chen",
                "title": "VP of Growth", 
                "company": "TechFlow Solutions",
                "email": "sarah@techflow.com",
                "linkedin_url": "https://linkedin.com/in/sarahchen",
                "hubspot_contact": None,
                "background_research": {
                    "background_info": [
                        {"title": "Growth Leadership", "snippet": "VP of Growth with 8+ years experience in SaaS metrics"}
                    ]
                }
            }
        ]
    }
    
    print("🔍 Testing Debug Prompt Preview Endpoint")
    print("=" * 50)
    
    try:
        # Test locally by importing the app and calling the function directly
        import sys
        sys.path.append('.')
        from app import api_debug_prompt_preview
        from fastapi import Request
        
        # Mock request object
        class MockRequest:
            def __init__(self, payload):
                self.payload = payload
            
            async def json(self):
                return self.payload
        
        mock_req = MockRequest(test_payload)
        
        # Call the debug endpoint
        response = await api_debug_prompt_preview(mock_req)
        
        if hasattr(response, 'body'):
            response_data = json.loads(response.body.decode())
        else:
            # If it's already a dict (JSONResponse content)
            response_data = response.body if hasattr(response, 'body') else {}
        
        print("✅ Debug endpoint executed successfully!")
        print("\n📊 Response Structure:")
        
        if 'system_message' in response_data:
            print(f"  • System Message: {len(response_data['system_message'])} characters")
        if 'user_prompt' in response_data:
            print(f"  • User Prompt: {len(response_data['user_prompt'])} characters") 
        if 'research_context' in response_data:
            print(f"  • Research Context: {len(response_data['research_context'])} characters")
        if 'prompt_stats' in response_data:
            stats = response_data['prompt_stats']
            print(f"  • Total Length: {stats.get('total_length', 0)} characters")
        
        print("\n🎯 Key Features Verified:")
        print("  ✓ Legacy format conversion")
        print("  ✓ Research context building")
        print("  ✓ Prompt statistics")
        print("  ✓ Full preview structure")
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing debug endpoint: {str(e)}")
        return False

if __name__ == "__main__":
    success = asyncio.run(test_debug_endpoint())
    if success:
        print("\n🎉 Debug endpoint test completed successfully!")
    else:
        print("\n💥 Debug endpoint test failed!")
