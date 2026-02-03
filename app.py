import os
import requests
import re
import string
import time
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# We will perform the search manually to ensure we can switch mirrors
# This removes the dependency on the flaky LibgenSearch library

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
LIBRARY_PATH = "/app/library"
if not os.path.exists(LIBRARY_PATH):
    os.makedirs(LIBRARY_PATH)

# --- MIRROR LIST ---
# The Monolith will try these in order until one works.
MIRRORS = [
    "http://libgen.is",
    "http://libgen.rs",
    "http://libgen.st",
    "http://libgen.li"
]

def clean_text(text):
    if not text: return "Unknown"
    text = " ".join(text.split()) 
    text = string.capwords(text)
    safe_text = re.sub(r'[\\/*?:"<>|]', "", text)
    return safe_text

def manual_search(query):
    """
    Manually searches LibGen mirrors using raw HTTP requests.
    This bypasses the broken/old library and handles ISP blocks.
    """
    out = []
    
    # 1. Try each mirror
    for mirror in MIRRORS:
        print(f"Monolith: Trying uplink to {mirror}...")
        try:
            # Construct search URL (Simple search, sorting by default)
            search_url = f"{mirror}/search.php?req={query}&res=25&view=simple&phrase=1&column=def"
            
            # Short timeout (10s) so we don't hang forever
            r = requests.get(search_url, timeout=10)
            
            if r.status_code != 200:
                print(f"Monolith: {mirror} returned status {r.status_code}. Skipping.")
                continue
                
            # 2. Parse HTML using Regex (Fast, no extra libraries needed)
            # We look for rows that contain download links
            # This regex looks for the table rows and extracts IDs and basic info
            # It's a bit "hacky" but very robust against library version issues.
            
            # Find all table rows
            # This pattern is specific to LibGen's search.php output
            # We are looking for the 'ID' which lets us build the download link
            
            # Simpler approach: Extract the MD5 hashes which are the keys to the files
            # LibGen search results usually have links like 'book/index.php?md5=...'
            
            md5_pattern = r'href="book/index\.php\?md5=([A-Fa-f0-9]{32})"'
            md5s = re.findall(md5_pattern, r.text)
            
            if not md5s:
                print(f"Monolith: Connected to {mirror} but found no artifacts (or parsing failed).")
                continue
                
            print(f"Monolith: Lock on! Found {len(md5s)} artifacts on {mirror}.")
            
            # Now we need to fetch details for these MD5s
            # LibGen has a hidden API: /json.php?ids=... or fields=...
            # We can use this to get clean metadata quickly!
            
            ids_to_check = ",".join(md5s[:15]) # Check first 15
            json_url = f"{mirror}/json.php?ids={ids_to_check}&fields=id,title,author,year,extension,md5,filesize"
            
            meta_r = requests.get(json_url, timeout=10)
            data = meta_r.json()
            
            for item in data:
                ext = item.get('extension', '').lower()
                if ext not in ['pdf', 'epub']: continue
                
                # Build the direct download gateway
                # The most reliable way is often http://library.lol/main/{md5}
                # But we can also use the mirror's own gateway
                md5 = item.get('md5')
                dl_url = f"http://library.lol/main/{md5}"
                
                out.append({
                    "title": clean_text(item.get('title')),
                    "author": clean_text(item.get('author')),
                    "year": item.get('year'),
                    "extension": ext,
                    "size": item.get('filesize'), # LibGen JSON might return bytes, but frontend handles string
                    "download_url": dl_url
                })
            
            # If we got results, stop trying mirrors and return
            if out:
                return out
                
        except Exception as e:
            print(f"Monolith: Uplink to {mirror} failed: {e}")
            continue
            
    return out

@app.route("/")
def home():
    return "The Monolith is Online. Use the portable client."

@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q: return jsonify({"error": "missing query"}), 400

    print(f"Monolith: Initiating scan for '{q}'...")
    start_time = time.time()

    try:
        results = manual_search(q)
        
        print(f"Monolith: Scan complete in {round(time.time() - start_time, 2)}s.")
        
        if not results:
            # If manual search failed, return empty list (not error) so frontend says "No results"
            return jsonify([])

        return jsonify(results)

    except Exception as e:
        print(f"Monolith: CRITICAL FAILURE -> {e}")
        return jsonify({"error": "Global scan failed.", "details": str(e)}), 500

@app.route("/api/download", methods=["POST"])
def download_book():
    data = request.json
    # The 'url' here is likely http://library.lol/main/MD5...
    # This page contains the ACTUAL download link (GET /)
    raw_url = data.get("url")
    
    author = clean_text(data.get("author", "Unknown Author"))
    title = clean_text(data.get("title", "Unknown Title"))
    year = data.get("year", "")
    ext = data.get("extension", "pdf")

    if not raw_url: return jsonify({"error": "No URL provided"}), 400

    author_dir = os.path.join(LIBRARY_PATH, author)
    if not os.path.exists(author_dir):
        os.makedirs(author_dir)

    filename = f"{title} ({year}).{ext}"
    filepath = os.path.join(author_dir, filename)

    if os.path.exists(filepath):
        return jsonify({"message": "Artifact already exists", "filename": filename})

    try:
        print(f"Monolith: Resolving gateway {raw_url}...")
        
        # 1. We need to get the "GET" link from the library.lol page
        # library.lol is a landing page, we need the link inside it that says "GET" or "Cloudflare"
        r_gateway = requests.get(raw_url, timeout=15)
        
        # Regex to find the download link
        # Usually: <a href="...">GET</a> or <h2><a href="...">Download</a></h2>
        # Let's find the FIRST link that looks like a file download
        
        # library.lol structure usually has a link at the top
        # We look for the 'href' inside the 'GET' link container
        link_pattern = r'<a href="(.*?)"'
        matches = re.findall(link_pattern, r_gateway.text)
        
        real_dl_url = None
        for m in matches:
            # Clean up link
            if not m.startswith("http"):
                # sometimes links are relative
                continue
            # Usually the first http link is the main download
            real_dl_url = m
            break
            
        if not real_dl_url:
            # Fallback: Just try the raw_url (unlikely to work for library.lol but worth a shot)
            print("Monolith: Could not resolve direct link, trying raw...")
            real_dl_url = raw_url

        print(f"Monolith: Acquiring from {real_dl_url}...")
        
        r_file = requests.get(real_dl_url, stream=True, timeout=300) # 5 min timeout for big files
        r_file.raise_for_status()
        
        with open(filepath, 'wb') as f:
            for chunk in r_file.iter_content(chunk_size=8192):
                f.write(chunk)
                
        print("Monolith: Acquisition Complete.")
        return jsonify({"success": True, "filename": filename})
        
    except Exception as e:
        print(f"Monolith: Download failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/library")
def get_library():
    files = []
    for root, dirs, filenames in os.walk(LIBRARY_PATH):
        for f in filenames:
            if f.startswith('.'): continue
            full_path = os.path.join(root, f)
            relative_path = os.path.relpath(full_path, LIBRARY_PATH).replace("\\", "/")
            author_name = os.path.basename(root)
            if root == LIBRARY_PATH: author_name = "Unsorted"
            name_parts = os.path.splitext(f)[0]
            ext = os.path.splitext(f)[1].replace(".", "")
            files.append({
                "filename": relative_path,
                "title": name_parts,
                "author": author_name,
                "extension": ext
            })
    files.sort(key=lambda x: (x['author'], x['title']))
    return jsonify(files)

@app.route("/files/<path:filename>")
def serve_book(filename):
    return send_from_directory(LIBRARY_PATH, filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9696, debug=True)
