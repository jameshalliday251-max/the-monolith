import os
import requests
import re
import string
import time
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# --- CONFIGURATION ---
LIBRARY_PATH = "/app/library"
if not os.path.exists(LIBRARY_PATH):
    os.makedirs(LIBRARY_PATH)

# --- COMPATIBLE MIRRORS ONLY ---
# We removed .li and others because they break the search pattern.
# We prioritize .is and .rs as they are the standard-bearers.
MIRRORS = [
    "http://libgen.is",
    "http://libgen.rs",
    "http://libgen.st",
    "http://www.libgen.is", 
    "http://www.libgen.rs"
]

def clean_text(text):
    if not text: return "Unknown"
    text = " ".join(text.split()) 
    text = string.capwords(text)
    safe_text = re.sub(r'[\\/*?:"<>|]', "", text)
    return safe_text

def manual_search(query):
    out = []
    
    # Try each mirror in order
    for mirror in MIRRORS:
        print(f"Monolith: Pinging {mirror}...")
        try:
            # Search URL for standard LibGen (Simple, Column=Default)
            search_url = f"{mirror}/search.php?req={query}&res=25&view=simple&phrase=1&column=def"
            
            # TIGHT TIMEOUT: Fail fast (5s) so we can try the next mirror quickly
            r = requests.get(search_url, timeout=5)
            
            if r.status_code != 200:
                print(f"Monolith: {mirror} unreachable (Status {r.status_code}).")
                continue

            # REGEX PARSING (Standard Layout Only)
            # 1. Find the MD5 hashes (The DNA of the file)
            # Pattern: matches href="book/index.php?md5=..."
            md5_pattern = r'href="book/index\.php\?md5=([A-Fa-f0-9]{32})"'
            md5s = re.findall(md5_pattern, r.text)
            
            if not md5s:
                # If we connected but found no MD5s, the site might be up but showing a captcha or error.
                # Or simply no results found.
                print(f"Monolith: Connection good, but no artifacts found on {mirror}.")
                continue # Try next mirror just in case
                
            print(f"Monolith: Lock on! Found {len(md5s)} artifacts on {mirror}.")
            
            # 2. Get Metadata (Using the bulk JSON API)
            # This is much faster than scraping every page
            ids_to_check = ",".join(md5s[:15]) 
            json_url = f"{mirror}/json.php?ids={ids_to_check}&fields=id,title,author,year,extension,md5,filesize"
            
            meta_r = requests.get(json_url, timeout=10)
            data = meta_r.json()
            
            for item in data:
                ext = item.get('extension', '').lower()
                if ext not in ['pdf', 'epub']: continue
                
                md5 = item.get('md5')
                # Use library.lol as the primary gateway (most reliable)
                dl_url = f"http://library.lol/main/{md5}"
                
                out.append({
                    "title": clean_text(item.get('title')),
                    "author": clean_text(item.get('author')),
                    "year": item.get('year'),
                    "extension": ext,
                    "size": item.get('filesize'),
                    "download_url": dl_url
                })
            
            # If we found data, we are done. Return it.
            if out: return out
                
        except Exception as e:
            print(f"Monolith: Link to {mirror} severed: {e}")
            continue
            
    return out

@app.route("/")
def home():
    return "The Monolith is Online."

@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q: return jsonify({"error": "missing query"}), 400

    print(f"Monolith: Global Scan initiated for '{q}'...")
    start_time = time.time()

    try:
        results = manual_search(q)
        print(f"Monolith: Scan finished in {round(time.time() - start_time, 2)}s.")
        
        # Always return a list, even if empty (prevents frontend errors)
        return jsonify(results)

    except Exception as e:
        print(f"Monolith: FATAL ERROR -> {e}")
        return jsonify({"error": "Global scan failed.", "details": str(e)}), 500

@app.route("/api/download", methods=["POST"])
def download_book():
    data = request.json
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
        
        # 1. Resolve the "GET" link from library.lol
        # We spoof the User-Agent to look like a real browser (fixes some blocks)
        headers = {'User-Agent': 'Mozilla/5.0'}
        r_gateway = requests.get(raw_url, headers=headers, timeout=15)
        
        link_pattern = r'<a href="(.*?)"'
        matches = re.findall(link_pattern, r_gateway.text)
        
        real_dl_url = None
        for m in matches:
            if not m.startswith("http"): continue
            real_dl_url = m
            break
            
        if not real_dl_url:
            real_dl_url = raw_url

        print(f"Monolith: Acquiring from {real_dl_url}...")
        
        r_file = requests.get(real_dl_url, headers=headers, stream=True, timeout=300)
        r_file.raise_for_status()
        
        with open(filepath, 'wb') as f:
            for chunk in r_file.iter_content(chunk_size=8192):
                f.write(chunk)
                
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
