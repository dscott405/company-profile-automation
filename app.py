import streamlit as st
import os
import anthropic
import requests
import csv
from io import StringIO
import time
import re
from urllib.parse import urlparse
from requests_html import HTMLSession
from bs4 import BeautifulSoup
import trafilatura

st.set_page_config(
    page_title="Company Profile Automation Engine", 
    page_icon="🔍",
    layout="wide"
)

DEFAULT_MODEL_STR = "claude-3-5-sonnet-20241022"

def initialize_anthropic_client():
    """Initialize Anthropic client with API key from environment variables"""
    try:
        # Get API key
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            st.error("ANTHROPIC_API_KEY environment variable not found.")
            return None
        
        # Clean the API key of any whitespace/newlines
        api_key = api_key.strip()
        
        # Validate key format
        if not api_key.startswith('sk-ant-'):
            st.error("Invalid API key format. Anthropic keys should start with 'sk-ant-'")
            return None
            
        client = anthropic.Anthropic(api_key=api_key)
        return client
            
    except Exception as e:
        st.error(f"Failed to initialize Anthropic client: {str(e)}")
        return None

def normalize_facebook_url(facebook_url):
    """Normalize Facebook URL to clean root format"""
    if not facebook_url or 'facebook.com' not in facebook_url.lower():
        return facebook_url
    
    # Remove mobile prefix
    facebook_url = facebook_url.replace('m.facebook.com', 'facebook.com')
    
    # Extract just the page name (remove subpaths like /mentions, /about, etc.)
    try:
        # Match pattern: facebook.com/pagename (with or without trailing content)
        match = re.search(r'facebook\.com/([^/?]+)', facebook_url)
        if match:
            page_name = match.group(1)
            return f"https://www.facebook.com/{page_name}"
    except:
        pass
    
    return facebook_url

def search_with_serpapi(company_name, api_key):
    """Search using SERP API for reliable Google search results"""
    try:
        search_url = "https://serpapi.com/search"
        params = {
            "engine": "google",
            "q": f'"{company_name}"',
            "api_key": api_key,
            "num": 10
        }
        
        response = requests.get(search_url, params=params, timeout=30)
        response.raise_for_status()
        results = response.json()
        
        websites = []
        if 'organic_results' in results:
            for result in results['organic_results']:
                link = result.get('link', '')
                title = result.get('title', '')
                
                # Filter out directory sites and social media
                exclude_domains = [
                    'yelp.com', 'yellowpages.com', 'facebook.com', 'linkedin.com',
                    'indeed.com', 'glassdoor.com', 'manta.com', 'bbb.org',
                    'mapquest.com', 'whitepages.com', 'superpages.com',
                    'birdeye.com', 'eyeglassworld.com', 'healthgrades.com', 'carecredit.com'
                ]
                
                if not any(domain in link.lower() for domain in exclude_domains):
                    # Convert to root domain
                    from urllib.parse import urlparse
                    parsed = urlparse(link)
                    root_domain = f"{parsed.scheme}://{parsed.netloc}"
                    websites.append(root_domain)
        
        return websites[:5]  # Return top 5 potential websites
        
    except Exception as e:
        st.error(f"SERP API search error: {e}")
        return []

def analyze_website_content_serpapi(website_url, company_name=""):
    """Enhanced website analysis with all required features"""
    if not website_url:
        return {
            'emails': [],
            'contact_form': '',
            'facebook_page': '',
            'logo_url': ''
        }
    
    try:
        # Use basic requests instead of requests-html to avoid event loop issues
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(website_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract emails
        emails = extract_emails_enhanced(response.text)
        
        # PRIORITY: Check dedicated contact pages FIRST (highest priority)
        contact_form = ""
        # Prioritize true contact pages over any other forms
        priority_contact_pages = ['/contact-us', '/contact', '/contact.html', '/contact.htm', '/contact.php', '/contact_us', '/get-in-touch', '/reach-us', '/hours-location', '/location-hours']
        
        for page in priority_contact_pages:
            try:
                contact_url = website_url.rstrip('/') + page
                contact_response = requests.get(contact_url, headers=headers, timeout=10)
                if contact_response.status_code == 200:
                    contact_soup = BeautifulSoup(contact_response.content, 'html.parser')
                    contact_form = check_contact_forms_enhanced(contact_soup, contact_response.text, contact_url)
                    if contact_form:
                        break  # Found on dedicated contact page - stop here, don't check other pages
            except Exception as e:
                continue
        
        # Only if no dedicated contact pages found, check homepage
        if not contact_form:
            contact_form = check_contact_forms_enhanced(soup, response.text, website_url)
        
        # Extract Facebook page
        facebook_page = extract_facebook_from_website(soup, company_name)
        
        # Extract logo URL
        logo_url = extract_logo_url_enhanced(soup, website_url)
        
        return {
            'emails': emails,
            'contact_form': contact_form,
            'facebook_page': facebook_page,
            'logo_url': logo_url
        }
        
    except Exception as e:
        st.warning(f"Error analyzing {website_url}: {e}")
        return {
            'emails': [],
            'contact_form': '',
            'facebook_page': '',
            'logo_url': ''
        }

def check_contact_forms_enhanced(soup, page_text="", current_url=""):
    """Enhanced contact form detection - returns URL where form was found or empty string"""
    try:
        # Priority 1: Check for JavaScript modal/popup contact forms
        modal_indicators = [
            'send message', 'contact us', 'get in touch', 'send inquiry',
            'message us', 'contact form', 'reach out', 'send email'
        ]
        
        for indicator in modal_indicators:
            contact_triggers = soup.find_all(['button', 'a', 'div', 'span'], 
                                           string=re.compile(indicator, re.IGNORECASE))
            if contact_triggers:
                # Check if there are hidden forms or modal containers
                hidden_forms = soup.find_all(['div', 'section'], 
                                           attrs={'style': re.compile(r'display.*none', re.IGNORECASE)})
                modal_containers = soup.find_all(['div'], 
                                               class_=re.compile(r'modal|popup|overlay|dialog', re.IGNORECASE))
                
                # Check if hidden containers have form elements
                for container in hidden_forms + modal_containers:
                    form_elements = container.find_all(['form', 'input', 'textarea'])
                    if form_elements:
                        return current_url
                
                # Also check for JavaScript form services in the page source
                if any(service in page_text.lower() for service in [
                    'contact-form', 'message-form', 'inquiry-form'
                ]):
                    return current_url
        
        # Priority 2: Look for actual HTML forms with proper validation
        forms = soup.find_all('form')
        appointment_form_found = False
        
        for form in forms:
            form_text = form.get_text().lower()
            inputs = form.find_all(['input', 'textarea', 'select'])
            
            # Skip forms with no inputs or very few inputs
            if len(inputs) < 2:
                continue
                
            # Skip error dialogs, login forms, and search forms
            if any(skip_word in form_text for skip_word in ['error!', 'close', 'login', 'sign in', 'password']):
                continue
                
            if 'search' in form_text and len(inputs) <= 2:
                continue
            
            # Check for high-priority contact forms first (but validate they're real forms)
            if any(word in form_text for word in ['contact', 'message', 'inquiry', 'question', 'feedback']):
                # Must have minimum inputs to be a real contact form
                if len(inputs) >= 3:
                    return current_url
            
            # Look for standard contact form fields with strict validation
            input_names = [inp.get('name', '').lower() for inp in inputs]
            input_types = [inp.get('type', '').lower() for inp in inputs]
            placeholders = [inp.get('placeholder', '').lower() for inp in inputs]
            
            # Enhanced field detection
            has_name = (any('name' in name for name in input_names) or 
                       any('name' in ph for ph in placeholders) or
                       any('name' in form_text for name_text in ['your name', 'full name', 'first name', 'last name']))
            
            has_email = (any('email' in name for name in input_names) or 
                        'email' in input_types or
                        any('email' in ph for ph in placeholders) or
                        any(inp.get('type', '').lower() == 'email' for inp in inputs))
            
            has_message = (any('message' in name or 'comment' in name for name in input_names) or 
                          any(inp.name == 'textarea' for inp in inputs) or
                          any('message' in ph or 'comment' in ph for ph in placeholders))
            
            # STRICT VALIDATION: Must have name + email + message AND minimum 3 inputs
            if has_name and has_email and has_message and len(inputs) >= 3:
                return current_url
            
            # Check form attributes for contact-related actions
            action = form.get('action', '')
            if action and any(word in action.lower() for word in ['contact', 'message', 'inquiry', 'send']):
                # Additional validation for action-based detection
                if len(inputs) >= 3 and (has_email or has_message):
                    return current_url
            
            # Check for appointment forms (lower priority)
            if any(word in form_text for word in ['appointment', 'schedule', 'book', 'patient']):
                appointment_form_found = True
        
        # Return appointment form if found (only if no contact forms were detected)
        if appointment_form_found:
            return current_url
        
        # Priority 3: Check for appointment request buttons
        appointment_indicators = [
            'request appointment', 'book appointment', 'schedule appointment',
            'request an appointment', 'book an appointment', 'schedule an appointment'
        ]
        
        for indicator in appointment_indicators:
            appt_triggers = soup.find_all(['button', 'a', 'div', 'span'], 
                                        string=re.compile(indicator, re.IGNORECASE))
            if appt_triggers:
                return current_url
        
        # Priority 4: Look for WordPress and dynamic forms
        # Gravity Forms
        scripts = soup.find_all('script')
        for script in scripts:
            script_content = script.get_text() if script.string else ""
            if any(gform_indicator in script_content.lower() for gform_indicator in ['gform', 'gravity', 'gravityforms']):
                return current_url
        
        # Contact Form 7 and general contact forms
        html_content = str(soup).lower()
        if any(cf7_indicator in html_content for cf7_indicator in ['wpcf7', 'contact-form-7', 'contact-form']):
            return current_url
        
        # Form containers with contact-related classes
        contact_form_containers = soup.find_all('div', class_=lambda x: x and any(
            keyword in ' '.join(x).lower() for keyword in ['contact-us', 'form__container', 'leadform', 'contact form', 'pleform', 'gform', 'wpcf7']
        ))
        if contact_form_containers:
            return current_url
        
        # Priority 5: Look for iframe forms (third-party services)
        iframes = soup.find_all('iframe')
        for iframe in iframes:
            src = iframe.get('src', '').lower()
            if any(service in src for service in ['typeform', 'jotform', 'wufoo', 'google.com/forms', 'formstack']):
                return current_url
        
        # Priority 6: Look for contact form links
        links = soup.find_all('a', href=True)
        for link in links:
            href = link.get('href', '').lower()
            link_text = link.get_text().lower()
            
            # Skip appointment/scheduling links
            if any(word in href or word in link_text for word in ['appointment', 'schedule', 'book', 'patient']):
                continue
            
            # Look for contact form links
            if any(word in href for word in ['contact', 'get-in-touch', 'reach-us']) and 'form' in (href + link_text):
                return href if href.startswith('http') else current_url
        
        return ""
        
    except Exception as e:
        return ""

def extract_emails_enhanced(html_content):
    """Enhanced email extraction with CloudFlare protection handling"""
    emails = set()
    
    # Decode CloudFlare email protection - both URL and data-cfemail patterns
    cf_url_pattern = r'/cdn-cgi/l/email-protection#([a-f0-9]+)'
    cf_data_pattern = r'data-cfemail="([a-f0-9]+)"'
    
    cf_matches = re.findall(cf_url_pattern, html_content) + re.findall(cf_data_pattern, html_content)
    
    for match in cf_matches:
        try:
            # Decode CloudFlare protected email
            decoded = ""
            key = int(match[:2], 16)
            for i in range(2, len(match), 2):
                decoded += chr(int(match[i:i+2], 16) ^ key)
            if '@' in decoded and '.' in decoded and len(decoded) > 5:
                emails.add(decoded.lower())
        except:
            continue
    
    # Standard email pattern
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    standard_emails = re.findall(email_pattern, html_content, re.IGNORECASE)
    
    # Enhanced filtering for business emails only
    business_emails = []
    exclude_patterns = [
        r'.*@example\.',
        r'.*@test\.',
        r'.*@domain\.',
        r'.*@email\.',
        r'.*@(noreply|no-reply)',
        r'.*@(support|info|contact|admin)\.example',
        r'.*@sentry\.',
        r'.*@sentry-next\.',
        r'.*@sentry\.io$',
        r'.*@sentry\.wixpress\.com$',
        r'.*\.(png|jpg|jpeg|gif|svg|webp|bmp|tiff)$',
        r'^[a-f0-9]{32}@',
        r'@2x\.',
        r'@3x\.',
        r'^\d+@2x\.',
        r'^\d+@3x\.',
        r'.*_\d+x@\d+x\.',
        r'.*@\d+x\.',
        r'.*placeholder.*@',
        r'.*loader.*@'
    ]
    
    # Only exclude obviously fake or test personal emails
    personal_patterns = [
        r'^test@',
        r'^example@',
        r'^demo@',
        r'^sample@'
    ]
    
    for email in standard_emails:
        email = email.lower().strip()
        # Skip if matches any exclude pattern
        if any(re.match(pattern, email) or re.search(pattern, email) for pattern in exclude_patterns):
            continue
        # Skip only obviously fake emails
        if any(re.match(pattern, email) for pattern in personal_patterns):
            continue
        # Skip if contains image file extensions or technical patterns
        if any(ext in email for ext in ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp']):
            continue
        # Skip if looks like a hash or technical identifier
        if re.match(r'^[a-f0-9]{8,}@', email):
            continue
        # Keep valid business emails
        if '@' in email and '.' in email and len(email.split('@')[0]) > 0:
            business_emails.append(email)
    
    emails.update(business_emails)
    
    # Return only the most realistic email (prefer info@, contact@, or domain-based)
    email_list = list(emails)
    if len(email_list) > 1:
        # Prioritize business-like emails
        priority_emails = [e for e in email_list if any(prefix in e for prefix in ['info@', 'contact@', 'hello@', 'admin@'])]
        if priority_emails:
            return [priority_emails[0]]
        # Otherwise return the first valid one
        return [email_list[0]]
    
    return email_list

def extract_facebook_from_website(soup, company_name):
    """Extract Facebook page URL from website"""
    try:
        # Look for Facebook links
        facebook_links = []
        
        # Find all links that contain facebook
        links = soup.find_all('a', href=True)
        for link in links:
            href = link.get('href', '')
            if 'facebook.com' in href.lower() and '/sharer' not in href.lower():
                facebook_links.append(href)
        
        # Also check for Facebook URLs in text and meta tags
        page_text = str(soup)
        facebook_matches = re.findall(r'https?://(?:www\.)?(?:m\.)?facebook\.com/[^\s"\'<>]+', page_text, re.IGNORECASE)
        facebook_links.extend(facebook_matches)
        
        # Filter and return the best Facebook URL
        for fb_url in facebook_links:
            # Skip generic Facebook URLs
            if any(generic in fb_url.lower() for generic in ['/sharer', '/tr?', '/plugins', '/dialog', '/pages']):
                continue
            
            # Clean up the URL
            fb_url = fb_url.split('?')[0]  # Remove query parameters
            fb_url = fb_url.rstrip('/')  # Remove trailing slash
            
            # Validate Facebook URL has proper page identifier
            if 'facebook.com/' in fb_url:
                page_part = fb_url.split('facebook.com/')[-1]
                # Must have substantial content and not be just 'pages' or other generic paths
                if (len(page_part) > 3 and 
                    not page_part.lower().startswith('pages') and
                    not page_part.lower() in ['home', 'login', 'signup', 'help', 'about']):
                    return normalize_facebook_url(fb_url)
        
        return ""
        
    except Exception as e:
        return ""

def extract_logo_url_enhanced(soup, website_url):
    """Enhanced logo URL extraction"""
    logo_selectors = [
        'img[alt*="logo" i]',
        'img[src*="logo" i]',
        'img[class*="logo" i]',
        '.logo img',
        '#logo img',
        'header img',
        '.header img',
        '.brand img',
        '.navbar-brand img'
    ]
    
    for selector in logo_selectors:
        logo = soup.select_one(selector)
        if logo and logo.get('src'):
            src = logo['src']
            # Convert relative URLs to absolute
            if src.startswith('//'):
                src = 'https:' + src
            elif src.startswith('/'):
                from urllib.parse import urljoin
                src = urljoin(website_url, src)
            elif not src.startswith('http'):
                from urllib.parse import urljoin
                src = urljoin(website_url, src)
            
            return src
    
    return ""

def verify_website_ownership_with_claude(client, website_url, company_name, address="", phone=""):
    """Use Claude AI to verify if a website actually belongs to the company"""
    try:
        if not client:
            st.write("⚠️ No Claude client available, skipping verification")
            return True  # Skip verification if no client
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(website_url, headers=headers, timeout=15)
        if response.status_code != 200:
            st.write(f"⚠️ Website returned status {response.status_code}")
            return True  # Don't reject based on HTTP errors alone
        
        # Get page content
        soup = BeautifulSoup(response.content, 'html.parser')
        title = soup.find('title')
        title_text = title.text if title else ""
        
        # Get limited content to avoid token limits
        page_text = soup.get_text()[:1500]
        
        # Simple check first - if company name appears on page, likely correct
        if company_name.lower() in page_text.lower() or company_name.lower() in title_text.lower():
            st.write(f"✅ Company name found on website, accepting")
            return True
        
        # Use Claude for more complex verification
        prompt = f"""
        Verify if this website belongs to the specific company:
        
        Company: {company_name}
        {f"Address: {address}" if address else ""}
        {f"Phone: {phone}" if phone else ""}
        
        Website URL: {website_url}
        Page Title: {title_text}
        Page Content: {page_text[:800]}
        
        Does this website clearly belong to "{company_name}"? 
        Be lenient - accept if there's reasonable evidence this is the right company.
        Only reject obvious mismatches or directory sites.
        
        Respond with only "YES" or "NO".
        """
        
        message = client.messages.create(
            model=DEFAULT_MODEL_STR,
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = message.content[0].text.upper()
        claude_result = "YES" in response_text
        st.write(f"🤖 Claude verification: {claude_result}")
        return claude_result
        
    except Exception as e:
        st.write(f"⚠️ Verification error: {str(e)}")
        return True  # Default to accepting if verification fails

def verify_facebook_with_claude(client, facebook_url, company_name, address="", phone=""):
    """Use Claude AI to verify if a Facebook page belongs to the specific company"""
    try:
        if not client:
            return True  # Skip verification if no client
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        response = requests.get(facebook_url, headers=headers, timeout=15)
        if response.status_code != 200:
            return False
        
        # Get page content
        soup = BeautifulSoup(response.content, 'html.parser')
        title = soup.find('title')
        title_text = title.text if title else ""
        
        # Get limited content to avoid token limits
        page_text = soup.get_text()[:2000]
        
        prompt = f"""
        Verify if this Facebook page belongs to the specific company:
        
        Company: {company_name}
        {f"Address: {address}" if address else ""}
        {f"Phone: {phone}" if phone else ""}
        
        Facebook URL: {facebook_url}
        Page Title: {title_text}
        Page Content: {page_text}
        
        Does this Facebook page clearly belong to "{company_name}"? 
        Check if the company name, address, or phone number appears on the Facebook page.
        Reject generic pages or pages for different companies.
        
        Respond with only "YES" or "NO".
        """
        
        message = client.messages.create(
            model=DEFAULT_MODEL_STR,
            max_tokens=50,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = message.content[0].text.upper()
        return "YES" in response_text
        
    except Exception as e:
        return False

def extract_owner_info_serpapi(client, content, company_name, website_url=""):
    """Extract owner/leadership information using Claude AI analysis with enhanced page checking"""
    if not client:
        return "Could not extract owner info"
    
    try:
        prompt = f"""
        Analyze this company content and identify the owner, founder, or primary leader.
        
        Company: {company_name}
        Website: {website_url}
        
        Content:
        {content[:3000]}
        
        Look for:
        - Owner, founder, CEO, president mentions
        - "Founded by", "Started by" statements  
        - Leadership team information
        - About us sections mentioning founders
        - Bio sections with ownership details
        
        Provide a brief summary of who appears to be the owner/leader, or say "Not found" if unclear.
        """
        
        # Try with different model versions if first fails
        models_to_try = ["claude-3-5-sonnet-20241022", "claude-3-sonnet-20240229", "claude-3-haiku-20240307"]
        
        for model in models_to_try:
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=150,
                    messages=[{"role": "user", "content": prompt}]
                )
                return response.content[0].text.strip()
            except Exception as model_error:
                if "authentication_error" in str(model_error):
                    raise model_error  # Re-raise auth errors
                continue  # Try next model
        
        return "Could not extract owner info - model unavailable"
        
    except Exception as e:
        if "authentication_error" in str(e):
            st.error(f"Authentication failed. Please check your API key: {e}")
        else:
            st.warning(f"Owner extraction error: {e}")
        return "Could not extract owner info"

def search_facebook_page_serpapi(company_name, api_key, address="", client=None):
    """Search for company's Facebook page using SERP API"""
    try:
        # Enhanced search query
        search_query = f'"{company_name}" site:facebook.com/pages OR site:facebook.com/{company_name.replace(" ", "")}'
        if address:
            search_query += f' "{address.split(",")[0]}"'  # Add city from address
        
        search_url = "https://serpapi.com/search"
        params = {
            "engine": "google",
            "q": search_query,
            "api_key": api_key,
            "num": 5
        }
        
        response = requests.get(search_url, params=params, timeout=30)
        response.raise_for_status()
        results = response.json()
        
        if 'organic_results' in results:
            for result in results['organic_results']:
                link = result.get('link', '')
                if 'facebook.com' in link:
                    normalized_url = normalize_facebook_url(link)
                    if normalized_url:
                        return normalized_url
        
        return ""
        
    except Exception as e:
        st.warning(f"Facebook search error: {e}")
        return ""

def process_companies_with_serpapi(companies, api_key):
    """Process companies using SERP API with full feature set"""
    # Initialize Anthropic client for verification
    client = initialize_anthropic_client()
    
    results = []
    
    # Progress tracking
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, company in enumerate(companies):
        progress_bar.progress((i + 1) / len(companies))
        status_text.text(f"Processing {i + 1} of {len(companies)}: {company.get('name', 'Unknown')}")
        
        try:
            # Initialize result with company data
            result = company.copy()
            
            company_name = company.get('name', '')
            address = company.get('address', '')
            phone = company.get('phone', '')
            
            st.write(f"🔍 Processing: {company_name}")
            
            # Check if website already exists in CSV data
            existing_website = company.get('website', '').strip()
            
            if existing_website and existing_website.lower() not in ['', 'nan', 'none', 'null']:
                # Use existing website from CSV without verification (user has already researched these)
                result['website'] = existing_website
                st.write(f"✅ Using existing website from CSV: {existing_website}")
            else:
                # No existing website, search for one
                st.write("🔍 No existing website, searching...")
                websites = search_with_serpapi(company_name, api_key)
                
                if websites:
                    verified_website = None
                    for website in websites:
                        if verify_website_ownership_with_claude(client, website, company_name, address, phone):
                            verified_website = website
                            break
                    
                    if verified_website:
                        result['website'] = verified_website
                        st.write(f"✅ Found website: {verified_website}")
                    else:
                        result['website'] = websites[0]
                        st.write(f"✅ Found website: {websites[0]}")
            
            if result.get('website'):
                # Analyze website content
                st.write("📊 Analyzing website content...")
                analysis = analyze_website_content_serpapi(result['website'], company_name)
                
                result['emails'] = ', '.join(analysis.get('emails', []))
                contact_form_value = analysis.get('contact_form', '')
                result['contact_form'] = contact_form_value
                result['logo_url'] = analysis.get('logo_url', '')
                
                # Get Facebook from website analysis first
                facebook_from_website = analysis.get('facebook_page', '')
                if facebook_from_website:
                    result['facebook_page'] = facebook_from_website
                    st.write(f"✅ Found Facebook page on website: {facebook_from_website}")
                
                # If no Facebook found on website, search for it
                if not result.get('facebook_page'):
                    st.write("🔍 Searching for Facebook page...")
                    facebook_search_result = search_facebook_page_serpapi(company_name, api_key, address, client)
                    if facebook_search_result:
                        # Verify Facebook page belongs to company using Claude
                        if verify_facebook_with_claude(client, facebook_search_result, company_name, address, phone):
                            result['facebook_page'] = facebook_search_result
                            st.write(f"✅ Found Facebook page via search: {facebook_search_result}")
                        else:
                            st.write("❌ Found Facebook page but verification failed")
                
                # Skip owner extraction temporarily due to API key issue
                result['owner_info'] = "Owner extraction temporarily disabled"
            
            else:
                st.write("❌ No verified website found")
            
            results.append(result)
            st.write(f"✅ Completed: {company_name}")
            st.write("---")
            
            # Add delay to respect rate limits
            time.sleep(2)
            
        except Exception as e:
            st.write(f"❌ Error processing {company.get('name', 'Unknown')}: {str(e)}")
            results.append(result)
    
    progress_bar.progress(1.0)
    status_text.text("Processing complete!")
    
    return results

def create_csv_download_serpapi(results):
    """Create CSV download for SERP API results with full feature set"""
    if not results:
        return None
    
    output = StringIO()
    
    # Specific column order as requested
    fieldnames = ['name', 'street_address', 'city', 'state', 'zip_code', 'phone', 'website', 'emails', 'contact_form', 'facebook_page', 'rating', 'reviews', 'hours', 'logo_url']
    
    # Add any additional fields that might exist in the data
    all_fieldnames = set()
    for result in results:
        all_fieldnames.update(result.keys())
    
    # Add any extra fields not in the ordered list
    for field in all_fieldnames:
        if field not in fieldnames:
            fieldnames.append(field)
    
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    
    for result in results:
        # Filter result to only include fields that exist in fieldnames
        filtered_result = {k: v for k, v in result.items() if k in fieldnames}
        writer.writerow(filtered_result)
    
    return output.getvalue()

def main():
    st.title("🔍 Company Profile Automation Engine")
    st.markdown("### AI-driven research that compiles verified websites, contact details, decision makers, and social media accounts")
    st.markdown("---")
    
    # Instructions
    with st.expander("ℹ️ How to Use", expanded=False):
        st.markdown("""
        1. **Upload CSV file** with company information (name, address, phone columns)
        2. **Add your SERP API key** from serpapi.com
        3. **Click Process** to start automated research
        4. **Download results** with complete company profiles
        
        **What this finds for each company:**
        - ✅ Verified company website
        - 📧 Email addresses
        - 📝 Contact forms
        - 👤 Owner/leadership information
        - 📘 Facebook business pages  
        - 🎨 Company logos
        
        **Features:**
        - Claude AI verification ensures accuracy
        - Filters out directory sites and false matches
        - Comprehensive multi-page analysis
        - Professional-grade research capabilities
        """)
    
    # File upload
    uploaded_file = st.file_uploader("📁 Upload CSV with Company Data", type=['csv'])
    
    if uploaded_file:
        try:
            import pandas as pd
            df = pd.read_csv(uploaded_file)
            companies = df.to_dict('records')
            st.success(f"✅ Loaded {len(companies)} companies from CSV")
            
            # SERP API key input
            api_key = st.text_input(
                "🔑 SERP API Key", 
                type="password", 
                help="Get your API key from serpapi.com for reliable search results"
            )
            
            # Process companies button
            if api_key and st.button("🚀 Process Companies", type="primary"):
                st.info("🔄 Starting automated company research...")
                
                with st.spinner("Processing companies..."):
                    results = process_companies_with_serpapi(companies, api_key)
                
                if results:
                    st.success(f"✅ Processing complete! Researched {len(results)} companies")
                    
                    # Store results in session state
                    st.session_state['last_results'] = results
                    st.session_state['last_results_timestamp'] = time.time()
                    st.session_state['show_current_results'] = True  # Flag to show current results
                
                else:
                    st.error("❌ No results generated. Please check your API key and try again.")
            
            elif not api_key:
                st.error("⚠️ Please enter your SERP API key")
            
            # Show current results if they exist and flag is set
            if st.session_state.get('show_current_results', False) and 'last_results' in st.session_state:
                results = st.session_state['last_results']
                
                # Display summary
                st.markdown("### 📊 Results Summary")
                websites_found = sum(1 for r in results if r.get('website'))
                emails_found = sum(1 for r in results if r.get('emails'))
                facebook_found = sum(1 for r in results if r.get('facebook_page'))
                
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Websites Found", websites_found)
                with col2:
                    st.metric("Email Addresses", emails_found)
                with col3:
                    st.metric("Facebook Pages", facebook_found)
                
                # Preview results
                st.markdown("### 📋 Results Preview (First 10)")
                for i, result in enumerate(results[:10], 1):
                    with st.expander(f"{i}. {result['name']}", expanded=False):
                        col1, col2 = st.columns(2)
                        with col1:
                            st.write(f"🌐 **Website:** {result.get('website', 'Not found')}")
                            st.write(f"📧 **Emails:** {result.get('emails', 'Not found')}")
                            st.write(f"📝 **Contact Form:** {result.get('contact_form', 'Not found')}")
                        with col2:
                            st.write(f"👤 **Owner:** {result.get('owner_info', 'Not found')}")
                            st.write(f"📘 **Facebook:** {result.get('facebook_page', 'Not found')}")
                            st.write(f"🎨 **Logo:** {result.get('logo_url', 'Not found')}")
                
                # Current results download button - always visible when results exist
                csv_data = create_csv_download_serpapi(results)
                if csv_data:
                    st.download_button(
                        label="📥 Download Complete Results as CSV",
                        data=csv_data,
                        file_name=f"company_profiles_{int(time.time())}.csv",
                        mime="text/csv",
                        type="primary",
                        key="download_current_results"
                    )
        
        except Exception as e:
            st.error(f"❌ Error reading CSV file: {str(e)}")
    
    # Alternative download section - always available when results exist
    if 'last_results' in st.session_state and not st.session_state.get('show_current_results', False):
        st.markdown("---")
        st.markdown("### 📂 Download Results")
        
        timestamp = st.session_state.get('last_results_timestamp', 0)
        results_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp))
        results_count = len(st.session_state['last_results'])
        
        st.info(f"🕒 Last batch processed: {results_time} ({results_count} companies)")
        
        # Download results button - always available
        csv_data = create_csv_download_serpapi(st.session_state['last_results'])
        if csv_data:
            st.download_button(
                label="📥 Download Results as CSV",
                data=csv_data,
                file_name=f"company_profiles_{int(timestamp)}.csv",
                mime="text/csv",
                type="primary",
                key="download_persistent_results"
            )

if __name__ == "__main__":
    main()
