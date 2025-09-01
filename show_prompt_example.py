#!/usr/bin/env python3
"""
Show a sample of the full prompt that gets sent to OpenAI for BD intelligence reports.
"""

# Sample data that would typically come from the UI
sample_company = "TechFlow Solutions"
sample_industry = "SaaS"
sample_meeting_context = "Strategic partnership discussion about improving trial-to-paid conversion rates and reducing customer churn through better analytics and testing."

sample_researched_attendees = [
    {
        "name": "Sarah Chen",
        "title": "VP of Growth",
        "company": "TechFlow Solutions",
        "email": "sarah@techflow.com",
        "linkedin_url": "https://linkedin.com/in/sarahchen",
        "linkedin_snippet": "VP of Growth with 8+ years experience in SaaS metrics, conversion optimization, and customer acquisition strategies.",
        "hubspot_contact": None,
        "background_research": {
            "background_info": [
                {"title": "Sarah Chen - Growth Leader Profile", "snippet": "Experienced growth executive with proven track record in B2B SaaS companies..."}
            ]
        }
    },
    {
        "name": "David Rodriguez",
        "title": "Chief Technology Officer", 
        "company": "TechFlow Solutions",
        "email": "david@techflow.com",
        "linkedin_url": "https://linkedin.com/in/davidrodriguez",
        "linkedin_snippet": "CTO focused on scalable technology infrastructure and data analytics platforms for SaaS companies.",
        "hubspot_contact": {"_id": "123456789", "firstname": "David", "lastname": "Rodriguez", "company": "TechFlow Solutions"},
        "background_research": {
            "background_info": [
                {"title": "David Rodriguez - Technical Leadership", "snippet": "Technology leader with expertise in building analytics platforms..."}
            ]
        }
    }
]

sample_prompt = """Create a strategic business development intelligence report using the research provided below.
Focus on identifying specific opportunities where Cro Metrics can drive measurable business impact through our comprehensive digital growth services.

CRITICAL: Map the target company's specific needs to Cro Metrics' current service offerings: Analytics, CRO, Creative Services, Customer Journey Analysis, Design & Build, Iris platform, Lifecycle & Email, Performance Marketing.

Position Cro Metrics as "Your Agency for All Things Digital Growth" with $1B client impact, 97.4% retention rate, and 10X average ROI. Reference relevant client success stories (Home Chef, Curology, Bombas, Calendly, UNICEF USA) and industry expertise when applicable.

ALWAYS reference specific Cro Metrics services that align with their business challenges and demonstrate how our current offerings can solve their specific problems."""

# The system message (what we just enhanced)
BD_SYSTEM_MESSAGE = """
You are Cro Metrics' External Business Development Meeting Intelligence Agent.
Goal: produce a comprehensive, strategic intelligence report (≈1500–2000 words) that positions us to win external BD meetings.
Audience: Cro Metrics executives preparing for high-stakes external meetings.
Tone: analytical, strategic, confident. Focus on actionable intelligence.

ABOUT CRO METRICS:
Cro Metrics is "Your Agency for All Things Digital Growth" - a leading conversion rate optimization and digital growth consultancy that designs strategic solutions to transform brands into growth engines. We help clients see stronger customer engagement and positive ROI within the first year.

Current Service Offerings (from https://crometrics.com/):

CORE SERVICES:
• Analytics: Empower your team with unified data insights for full-funnel visibility and action
• Conversion Rate Optimization: Uncover your strongest growth opportunities while mitigating risks before they impact your bottom line
• Creative Services: Creative designed to captivate, convert, and drive growth results
• Customer Journey Analysis: Transform fragmented customer data into actionable insights
• Design and Build: From high-converting landing pages to (risk-free) re-platforming, and everything in between
• Iris by Cro Metrics: A single platform to manage and maximize the impact of your growth program
• Lifecycle and Email: Elevate loyalty and retention with cross-channel programs driving engagement and growth
• Performance Marketing: Maximize ROAS with data-driven, multi-channel campaigns and clear attribution

SPECIALIZED INDUSTRY EXPERTISE:
• Subscription-based companies
• E-Commerce/Retail
• SaaS and Lead Generation
• Hospitality
• FinTech
• B2B Lead Gen
• Nonprofit & Associations

PROVEN RESULTS & DIFFERENTIATORS:
• $1B total client impact across portfolio
• 97.4% retention rate with enterprise clients
• 10X average ROI per client
• 2X industry average for testing win rate
• Scientific approach: "We Don't Guess, We Test"
• Proprietary Iris platform for unified insights and predictive analysis
• Google Partner and Meta Business Partner certifications

CLIENT SUCCESS EXAMPLES:
• Home Chef: Boosted revenue and long-term success
• Curology: Creative that converts with data-driven design
• Bombas: Increased testing velocity and overall ROI
• Calendly: Access to best practices and strategies
• UNICEF USA: Thorough attention to detail and big-picture understanding

[Additional prompt sections about output format, guardrails, etc...]
"""

def build_research_context():
    """Build the research context that gets sent with the prompt."""
    research_sections = []
    
    # Company overview
    research_sections.append("## Company Overview Research")
    research_sections.append("**TechFlow Solutions - SaaS Platform Overview**")
    research_sections.append("Source: https://techflow.com")
    research_sections.append("B2B SaaS company providing workflow automation tools. Recent funding round of $25M. Focus on enterprise customers. Struggling with trial-to-paid conversion rates...")
    research_sections.append("")
    
    # Recent news
    research_sections.append("## Recent News & Developments")
    research_sections.append("**TechFlow Solutions Raises $25M Series B**")
    research_sections.append("Source: https://techcrunch.com/example")
    research_sections.append("Company raised Series B to accelerate product development and improve customer conversion metrics...")
    research_sections.append("")
    
    # Attendee profiles
    research_sections.append("## Meeting Attendee Profiles")
    for attendee in sample_researched_attendees:
        research_sections.append(f"### {attendee['name']}")
        research_sections.append(f"**Title:** {attendee['title']}")
        research_sections.append(f"**Email:** {attendee['email']}")
        research_sections.append(f"**LinkedIn:** {attendee['linkedin_url']}")
        
        if attendee['hubspot_contact']:
            contact_id = attendee['hubspot_contact'].get('_id', 'N/A')
            research_sections.append(f"**HubSpot Status:** Existing contact found (ID: {contact_id})")
        else:
            research_sections.append("**HubSpot Status:** Not in HubSpot")
        
        research_sections.append("**Professional Background:**")
        research_sections.append(f"- {attendee['linkedin_snippet']}")
        research_sections.append("")
    
    return "\n".join(research_sections)

def show_full_prompt():
    """Show the complete prompt structure sent to OpenAI."""
    print("🔍 FULL OPENAI PROMPT PREVIEW")
    print("=" * 80)
    
    # Build research context
    research_context = build_research_context()
    
    # Build composed context (exactly as sent to OpenAI)
    attendee_summary = ", ".join([f"{a['name']} ({a['title']})" for a in sample_researched_attendees])
    composed_context = (
        f"TARGET COMPANY: {sample_company}\n"
        f"MEETING ATTENDEES: {attendee_summary}\n"
        f"INDUSTRY: {sample_industry}\n"
        f"MEETING CONTEXT: {sample_meeting_context}\n\n"
        f"RESEARCH INTELLIGENCE:\n{research_context}"
    )
    
    print("\n📋 SYSTEM MESSAGE (role: developer)")
    print("=" * 50)
    print(BD_SYSTEM_MESSAGE[:1000] + "...")
    print(f"[System message total length: {len(BD_SYSTEM_MESSAGE):,} characters]")
    
    print("\n📋 USER PROMPT (role: user)")
    print("=" * 50)
    full_user_prompt = sample_prompt + "\n\n" + composed_context
    print(sample_prompt)
    print("\n" + "="*30 + " RESEARCH CONTEXT " + "="*30)
    print(composed_context[:1500] + "...")
    print(f"[User prompt total length: {len(full_user_prompt):,} characters]")
    
    print("\n📊 PROMPT STATISTICS")
    print("=" * 30)
    print(f"System Message: {len(BD_SYSTEM_MESSAGE):,} characters")
    print(f"User Prompt: {len(sample_prompt):,} characters") 
    print(f"Research Context: {len(composed_context):,} characters")
    print(f"Total Prompt: {len(BD_SYSTEM_MESSAGE) + len(full_user_prompt):,} characters")
    
    print(f"\n🎯 KEY ENHANCEMENTS IN SYSTEM MESSAGE:")
    print("• Complete Cro Metrics service portfolio")
    print("• $1B client impact and proven results")
    print("• Industry-specific expertise")
    print("• Client success stories (Home Chef, Curology, etc.)")
    print("• Iris platform differentiation")
    print("• Scientific approach: 'We Don't Guess, We Test'")
    
    print(f"\n🎯 KEY INSTRUCTIONS IN USER PROMPT:")
    print("• Map customer needs to specific Cro Metrics services")
    print("• Reference relevant client success stories")
    print("• Position as 'Your Agency for All Things Digital Growth'")
    print("• Demonstrate how current offerings solve specific problems")

if __name__ == "__main__":
    show_full_prompt()
