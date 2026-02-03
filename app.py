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

# --- MIRRORS (BLOCKADE RUNNER EDITION) ---
# We now include direct IP addresses and alternative domains to bypass ISP filters.
MIRRORS = [
    "http://libgen.is",          # Standard (Blocked by some)
    "http://185.39.10.101",      # Direct IP (Bypasses DNS Block)
    "http://libgen.rs",          # Backup
    "http://libgen.st",          # Backup
    "http://libgen.li"           # Alternative (Last resort)
]

# --- STEALTH HEADERS ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8'
}

def clean_text(text):
    if not text: return "Unknown"
    text = " ".join(text.split()) 
    text = string.capwords(text)
    safe_text = re.sub(r'[\\/*?:"<>|]', "", text)
    return safe_text

@app.route("/")
def home():
    return "The Monolith is Online. System Normal."

@app.route("/api/health")
def health_check():
    report = {"status": "online", "internet": "unknown", "libgen": "unknown"}
    try:
        requests.get("http://www.google.com", timeout=3)
        report["internet"] = "success"
    except: report["internet"] = "failed"
    
    # Try the IP mirror specifically for health check
    try:
        r = requests.get("http://185.39.10.101", headers=HEADERS, timeout=5)
        report["libgen"] = "success" if r.status_code == 200 else f"status_{r.status_code}"
    except Exception as e:
        report["libgen"] = f"failed: {str(e)}"
    return jsonify(report)

@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q: return jsonify({"error": "missing query"}), 400

    print(f"Monolith: Blockade Runner Scan for '{q}'...")
    
    out = []
    
    for mirror in MIRRORS:
        try:
            print(f"Monolith: Pinging {mirror}...")
            # Search URL
            search_url = f"{mirror}/search.php?req={q}&res=25&view=simple&phrase=1&column=def"
            r = requests.get(search_url, headers=HEADERS, timeout=8)
            
            if r.status_code != 200: continue

            # Regex for MD5
            md5_pattern = r'href="book/index\.php\?md5=([A-Fa-f0-9]{32})"'
            md5s = re.findall(md5_pattern, r.text)
            
            if not md5s: continue
            
            print(f"Monolith: Connection established via {mirror}.")
            
            # Fetch Metadata
            ids_to_check = ",".join(md5s[:15]) 
            json_url = f"{mirror}/json.php?ids={ids_to_check}&fields=id,title,author,year,extension,md5,filesize"
            
            meta_r = requests.get(json_url, headers=HEADERS, timeout=10)
            data = meta_r.json()
            
            for item in data:
                ext = item.get('extension', '').lower()
                if ext not in ['pdf', 'epub']: continue
                
                md5 = item.get('md5')
                # Use library.lol as primary gateway
                dl_url = f"http://library.lol/main/{md5}"
                
                out.append({
                    "title": clean_text(item.get('title')),
                    "author": clean_text(item.get('author')),
                    "year": item.get('year'),
                    "extension": ext,
                    "size": item.get('filesize'),
                    "download_url": dl_url
                })
            
            if out: return jsonify(out)
                
        except Exception as e:
            print(f"Monolith: {mirror} failed: {e}")
            continue
            
    return jsonify([])

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
        # Resolve Gateway
        r_gateway = requests.get(raw_url, headers=HEADERS, timeout=15)
        link_pattern = r'<a href="(.*?)"'
        matches = re.findall(link_pattern, r_gateway.text)
        
        real_dl_url = raw_url
        for m in matches:
            if m.startswith("http"): 
                real_dl_url = m
                break

        print(f"Monolith: Downloading from {real_dl_url}...")
        r_file = requests.get(real_dl_url, headers=HEADERS, stream=True, timeout=300)
        r_file.raise_for_status()
        
        with open(filepath, 'wb') as f:
            for chunk in r_file.iter_content(chunk_size=8192):
                f.write(chunk)
                
        return jsonify({"success": True, "filename": filename})
        
    except Exception as e:
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
