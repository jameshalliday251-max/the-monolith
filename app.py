import os
import requests
import re
import string
from flask import Flask, request, jsonify, send_file, send_from_directory
from flask_cors import CORS
from libgen_api_enhanced import LibgenSearch

app = Flask(__name__)
CORS(app)

# --- THE MONOLITH STORAGE ---
LIBRARY_PATH = os.path.join(os.getcwd(), "library")
if not os.path.exists(LIBRARY_PATH):
    os.makedirs(LIBRARY_PATH)

s = LibgenSearch()

def clean_text(text):
    if not text: return "Unknown"
    text = " ".join(text.split()) 
    text = string.capwords(text)
    safe_text = re.sub(r'[\\/*?:"<>|]', "", text)
    return safe_text

@app.route("/")
def home():
    return send_file("index.html")

@app.route("/reader")
def reader():
    return send_file("reader.html")

# --- API: RENAME ---
@app.route("/api/rename", methods=["POST"])
def rename_book():
    data = request.json
    # Format: "Author/OldFilename.epub"
    old_rel_path = data.get("filename") 
    new_title = clean_text(data.get("new_title"))
    
    if not old_rel_path or not new_title:
        return jsonify({"error": "Missing data"}), 400

    old_full_path = os.path.join(LIBRARY_PATH, old_rel_path)
    if not os.path.exists(old_full_path):
        return jsonify({"error": "Artifact not found"}), 404
        
    directory = os.path.dirname(old_full_path)
    _, ext = os.path.splitext(old_full_path)
    
    # New filename: "New Title.ext"
    new_filename = f"{new_title}{ext}"
    new_full_path = os.path.join(directory, new_filename)
    
    try:
        os.rename(old_full_path, new_full_path)
        # Return new path so frontend handles it
        new_rel_path = os.path.relpath(new_full_path, LIBRARY_PATH).replace("\\", "/")
        return jsonify({"success": True, "new_filename": new_rel_path, "new_title": new_title})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- API: SEARCH ---
@app.route("/api/search")
def search():
    q = request.args.get("q", "").strip()
    if not q: return jsonify({"error": "missing query"}), 400

    try:
        raw_results = s.search_default(q)
        out = []
        count = 0
        for book in raw_results:
            ext = book.extension.lower()
            if ext not in ['pdf', 'epub']: continue
            try:
                book.resolve_direct_download_link()
                out.append({
                    "title": clean_text(book.title),
                    "author": clean_text(book.author),
                    "year": book.year,
                    "extension": ext,
                    "size": book.size,
                    "download_url": book.resolved_download_link or book.tor_download_link
                })
                count += 1
                if count >= 12: break
            except: continue
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- API: DOWNLOAD ---
@app.route("/api/download", methods=["POST"])
def download_book():
    data = request.json
    url = data.get("url")
    author = clean_text(data.get("author", "Unknown Author"))
    title = clean_text(data.get("title", "Unknown Title"))
    year = data.get("year", "")
    ext = data.get("extension", "pdf")

    if not url: return jsonify({"error": "No URL provided"}), 400

    # Organize by Author
    author_dir = os.path.join(LIBRARY_PATH, author)
    if not os.path.exists(author_dir):
        os.makedirs(author_dir)

    filename = f"{title} ({year}).{ext}"
    filepath = os.path.join(author_dir, filename)

    if os.path.exists(filepath):
        return jsonify({"message": "Artifact already exists", "filename": filename})

    try:
        print(f"Monolith: Acquiring {filename}...")
        r = requests.get(url, stream=True)
        r.raise_for_status()
        with open(filepath, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return jsonify({"success": True, "filename": filename})
    except Exception as e:
        print(f"Monolith: Download failed: {e}")
        return jsonify({"error": str(e)}), 500

# --- API: LIST LIBRARY ---
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
    # Sort by Author
    files.sort(key=lambda x: (x['author'], x['title']))
    return jsonify(files)

# --- FILE SERVER ---
@app.route("/files/<path:filename>")
def serve_book(filename):
    return send_from_directory(LIBRARY_PATH, filename)

if __name__ == "__main__":

    app.run(host="0.0.0.0", port=9696, debug=True)
