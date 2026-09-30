import streamlit as st
import folium
from streamlit_folium import st_folium
import pandas as pd
import json
import os
import io
import time
import requests
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from ai_assistant import render_ai_chat_section
import streamlit.components.v1 as components

st.set_page_config(
    page_title="Gau Sampurna - Outlet Coverage & Logistics Dashboard",
    page_icon="🗺️",
    layout="wide"
)

# Keep-Alive JavaScript Component to prevent background tab throttling & WebSocket timeouts
components.html(
    """
    <script>
    (function() {
        if (window.parent && window.parent.keepAliveTimer) return;
        const ping = function() {
            try {
                fetch(window.location.href, { method: 'HEAD', mode: 'no-cors', cache: 'no-store' });
            } catch(e) {}
        };
        if (window.parent) {
            window.parent.keepAliveTimer = setInterval(ping, 10000);
        } else {
            window.keepAliveTimer = setInterval(ping, 10000);
        }
    })();
    </script>
    """,
    height=0,
    width=0
)

# Custom header styling
st.markdown(
    """
    <div style="display: flex; align-items: center; justify-content: space-between; padding-bottom: 10px; border-bottom: 2px solid #e0e0e0; margin-bottom: 20px;">
        <div>
            <h1 style="margin: 0; padding: 0; font-size: 28px; color: #1b5e20;">🗺️ Gau Sampurna — Outlet Coverage & Logistics Dashboard</h1>
            <p style="margin: 4px 0 0 0; color: #555; font-size: 14px;">Logistics Network Intelligence • Suggested Nearest Outlets • Distance Calculators • Interactive Folium Map</p>
        </div>
    </div>
    """,
    unsafe_allow_html=True
)

SHEET_ID = "1Pmi5KMMbNfR1Zc9EXmvkEEKqkmWbXqLdJXt-UxqJ2Es"

# ----------------- DISK CACHING & CHECKPOINT HELPERS ----------------- #
SUGGESTED_CHECKPOINT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoint_suggested.json")
NEAREST_BULK_CHECKPOINT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoint_nearest_bulk.json")
BATCH_FIXED_CHECKPOINT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoint_batch_fixed.json")

def load_disk_pincode_cache():
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pincode_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                res = {}
                for k, v in data.items():
                    if isinstance(v, (list, tuple)) and len(v) == 2:
                        res[str(k)] = (float(v[0]), float(v[1]))
                return res
        except Exception:
            pass
    return {}

def save_disk_pincode_cache(cache_dict):
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pincode_cache.json")
    try:
        serializable = {k: list(v) if isinstance(v, (tuple, list)) else v for k, v in cache_dict.items()}
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)
    except Exception:
        pass

def save_checkpoint(filepath, df):
    try:
        if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
            df.to_json(filepath, orient="records", date_format="iso")
    except Exception:
        pass

def load_checkpoint(filepath):
    try:
        if os.path.exists(filepath):
            df = pd.read_json(filepath, orient="records")
            if not df.empty:
                return df
    except Exception:
        pass
    return None

def clear_checkpoint(filepath):
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
    except Exception:
        pass

# Initialize Session State (Clean Fresh State per User Session)
if 'outlets' not in st.session_state:
    st.session_state.outlets = []
if 'distance_result' not in st.session_state:
    st.session_state.distance_result = None
if 'batch_result_df' not in st.session_state:
    st.session_state.batch_result_df = None
if 'suggested_outlets_df' not in st.session_state:
    st.session_state.suggested_outlets_df = None
if 'pincode_cache' not in st.session_state:
    st.session_state.pincode_cache = load_disk_pincode_cache()
if 'nearest4_result' not in st.session_state:
    st.session_state.nearest4_result = None
if 'nearest_bulk_df' not in st.session_state:
    st.session_state.nearest_bulk_df = None


# ----------------- HELPER FUNCTIONS ----------------- #

DEFAULT_HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def safe_requests_get(url, headers=None, timeout=6, retries=2, backoff=0.4):
    """Executes requests.get safely with custom headers, timeout, and retry backoff."""
    req_headers = DEFAULT_HTTP_HEADERS.copy()
    if headers:
        req_headers.update(headers)
    for attempt in range(retries + 1):
        try:
            res = requests.get(url, headers=req_headers, timeout=timeout)
            if res.status_code == 200:
                return res
        except Exception:
            if attempt < retries:
                time.sleep(backoff * (2 ** attempt))
    return None

def sanitize_error_msg(err):
    """Cleans up raw technical exception messages to user-friendly text."""
    if not err:
        return "Pincode not found"
    err_str = str(err)
    if any(k in err_str for k in ["HTTPSConnectionPool", "Max retries exceeded", "Connection", "RemoteDisconnected", "Timeout", "NameResolutionError", "GeocoderTimedOut", "GeocoderServiceError"]):
        return "Location Service Timeout"
    return err_str

def clean_pincode_str(val):
    """Sanitizes pincode inputs into clean 6-digit strings."""
    if pd.isna(val):
        return ""
    s = str(val).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.strip()

def is_valid_india_coords(lat, lon):
    """Verifies that latitude and longitude fall within Indian territory."""
    try:
        lat_f, lon_f = float(lat), float(lon)
        return 6.0 <= lat_f <= 38.5 and 68.0 <= lon_f <= 98.0
    except Exception:
        return False

@st.cache_data(ttl=300)
def fetch_outlets_from_gsheet(sheet_id):
    """Fetches outlets from Google Sheets or falls back to local outlets_data.json."""
    cred_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
    has_creds_file = os.path.exists(cred_path)
    
    secrets_creds_dict = None
    try:
        if "gcp_service_account" in st.secrets:
            secrets_creds_dict = dict(st.secrets["gcp_service_account"])
        elif "credentials_json" in st.secrets:
            secrets_creds_dict = json.loads(st.secrets["credentials_json"])
    except Exception:
        secrets_creds_dict = None
    
    outlets = []
    messages = []
    
    if not has_creds_file and not secrets_creds_dict:
        local_json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outlets_data.json")
        if os.path.exists(local_json_path):
            try:
                with open(local_json_path, "r", encoding="utf-8") as f:
                    outlets = json.load(f)
                messages.append(("info", "Loaded outlets from local cache (outlets_data.json)."))
                return outlets, messages
            except Exception:
                pass
        messages.append(("error", "⚠️ Google credentials not found. Place `credentials.json` in project root or configure `st.secrets`."))
        return outlets, messages
        
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets.readonly',
            'https://www.googleapis.com/auth/drive.readonly'
        ]
        
        if secrets_creds_dict:
            credentials = Credentials.from_service_account_info(secrets_creds_dict, scopes=scopes)
        else:
            credentials = Credentials.from_service_account_file(cred_path, scopes=scopes)
            
        gc = gspread.authorize(credentials)
        sh = gc.open_by_key(sheet_id)
        worksheets = sh.worksheets()
        
        dfs = []
        for ws in worksheets:
            try:
                data = ws.get_all_records()
                if data:
                    df = pd.DataFrame(data)
                    df.columns = [str(c).lower().strip() for c in df.columns]
                    
                    col_map = {
                        'outlet name': 'name',
                        'outlet': 'name',
                        'latitude': 'lat',
                        'longitude': 'lon',
                        'long': 'lon'
                    }
                    df.rename(columns=col_map, inplace=True)
                    
                    required_cols = ['name', 'lat', 'lon']
                    missing = [col for col in required_cols if col not in df.columns]
                    
                    if not missing:
                        if 'state' not in df.columns:
                            df['state'] = ws.title
                        dfs.append(df)
                    else:
                        messages.append(("warning", f"Skipped tab '{ws.title}'. Missing columns: {', '.join(missing)}."))
            except Exception as e:
                messages.append(("warning", f"Could not read tab '{ws.title}': {e}"))
                
        if dfs:
            df_gsheet = pd.concat(dfs, ignore_index=True)
            for _, row in df_gsheet.iterrows():
                radius_val = row.get('radius_km', 60.0)
                try:
                    radius = float(radius_val) if radius_val != '' else 60.0
                    if pd.isna(radius): radius = 60.0
                except:
                    radius = 60.0
                    
                state_val = row.get('state', 'Unknown State')
                state_val = str(state_val) if state_val != '' and not pd.isna(state_val) else 'Unknown State'
                
                try:
                    lat_val = float(row['lat'])
                    lon_val = float(row['lon'])
                    
                    outlets.append({
                        "name": str(row['name']),
                        "state": state_val,
                        "lat": lat_val,
                        "lon": lon_val,
                        "radius_km": radius
                    })
                except (ValueError, TypeError):
                    pass
                    
            # Save local backup
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "outlets_data.json"), "w", encoding="utf-8") as f:
                    json.dump(outlets, f)
            except Exception:
                pass
    except Exception as e:
        messages.append(("error", f"Error accessing Google Sheets: {e}"))
        local_json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outlets_data.json")
        if os.path.exists(local_json_path):
            try:
                with open(local_json_path, "r", encoding="utf-8") as f:
                    outlets = json.load(f)
                messages.append(("info", "Falling back to local outlets_data.json cache."))
            except Exception:
                pass
                
    return outlets, messages


def geocode_pincode(pincode, geolocator, cache_dict):
    """Geocodes a pincode with in-memory & disk caching, multi-tier fallbacks, retries, and boundary validation."""
    clean_pc = clean_pincode_str(pincode)
    if not clean_pc:
        return None, "Empty Pincode"
        
    if clean_pc in cache_dict:
        return cache_dict[clean_pc], None
        
    coords = None
    err = None

    # Tier 1: Zippopotam API (Fast, direct lat/lon for Indian Pincodes)
    try:
        url = f"https://api.zippopotam.us/in/{clean_pc}"
        res = safe_requests_get(url, timeout=4, retries=1)
        if res and res.status_code == 200:
            data = res.json()
            places = data.get("places", [])
            if places:
                lat = float(places[0].get("latitude"))
                lon = float(places[0].get("longitude"))
                if is_valid_india_coords(lat, lon):
                    coords = (lat, lon)
    except Exception:
        pass

    # Tier 2: India Post API fallback (Extract District & State with User-Agent header)
    if not coords:
        try:
            res = safe_requests_get(f"https://api.postalpincode.in/pincode/{clean_pc}", timeout=6, retries=1)
            if res and res.status_code == 200:
                data = res.json()
                if isinstance(data, list) and len(data) > 0 and data[0].get("Status") == "Success":
                    post_offices = data[0].get("PostOffice", [])
                    if post_offices:
                        po_name = post_offices[0].get("Name", "")
                        district = post_offices[0].get("District", "")
                        state = post_offices[0].get("State", "")
                        
                        search_terms = []
                        if po_name and district and state:
                            search_terms.append(f"{po_name}, {district}, {state}, India")
                        if district and state:
                            search_terms.append(f"{district}, {state}, India")
                        elif state:
                            search_terms.append(f"{state}, India")
                            
                        for search_term in search_terms:
                            try:
                                loc3 = geolocator.geocode(search_term, timeout=8)
                                if loc3 and is_valid_india_coords(loc3.latitude, loc3.longitude):
                                    coords = (loc3.latitude, loc3.longitude)
                                    break
                            except Exception:
                                pass
        except Exception:
            pass

    # Tier 3: Standard Nominatim direct lookup
    if not coords:
        try:
            loc = geolocator.geocode(f"{clean_pc}, India", timeout=8)
            if loc and is_valid_india_coords(loc.latitude, loc.longitude):
                coords = (loc.latitude, loc.longitude)
        except Exception as e:
            err = sanitize_error_msg(e)
            
    # Tier 4: Nominatim postalcode filter lookup
    if not coords:
        try:
            loc2 = geolocator.geocode({"postalcode": clean_pc, "country": "India"}, timeout=8)
            if loc2 and is_valid_india_coords(loc2.latitude, loc2.longitude):
                coords = (loc2.latitude, loc2.longitude)
        except Exception as e:
            err = err or sanitize_error_msg(e)

    if coords:
        cache_dict[clean_pc] = coords
        save_disk_pincode_cache(cache_dict)
        return coords, None
    else:
        return None, sanitize_error_msg(err or "Pincode not found in India")



def find_nearest_outlet_for_pincode(pincode, valid_outlets, geolocator, cache_dict):
    """
    Computes distances to all outlets and determines the nearest suggested outlet.
    """
    clean_pc = clean_pincode_str(pincode)
    coords, err = geocode_pincode(clean_pc, geolocator, cache_dict)
    
    if not coords:
        return {
            "found": False,
            "pincode": clean_pc,
            "error": sanitize_error_msg(err or "Location Not Found")
        }
        
    pincode_lat, pincode_lon = coords
    
    # 1. Fast Great-Circle (Aerial) Distance Pre-sort
    candidates = []
    for o in valid_outlets:
        try:
            o_lat, o_lon = float(o['lat']), float(o['lon'])
            a_dist = geodesic((pincode_lat, pincode_lon), (o_lat, o_lon)).km
            candidates.append({
                **o,
                'lat': o_lat,
                'lon': o_lon,
                'aerial_km': a_dist
            })
        except Exception:
            continue
            
    if not candidates:
        return {
            "found": False,
            "pincode": clean_pc,
            "error": "No valid outlets available"
        }
        
    candidates.sort(key=lambda x: x['aerial_km'])
    best_candidate = candidates[0]
    
    # 2. Road Distance via OSRM for nearest candidate
    road_km = None
    route_coords = []
    try:
        url = f"http://router.project-osrm.org/route/v1/driving/{best_candidate['lon']},{best_candidate['lat']};{pincode_lon},{pincode_lat}?overview=full&geometries=geojson"
        res = safe_requests_get(url, timeout=6)
        if res and res.status_code == 200:
            data = res.json()
            if data.get('code') == 'Ok':
                road_km = data['routes'][0]['distance'] / 1000.0
                geometry = data['routes'][0]['geometry']['coordinates']
                route_coords = [(lat, lon) for lon, lat in geometry]
    except Exception:
        pass
        
    if road_km is None:
        # Fallback to estimated road distance (aerial * 1.25 curvature factor)
        road_km = best_candidate['aerial_km'] * 1.25
        
    radius = float(best_candidate.get('radius_km', 60.0))
    is_covered = road_km <= radius
    status = "Covered" if is_covered else "Out of Radius"
    
    return {
        "found": True,
        "pincode": clean_pc,
        "pincode_coords": (pincode_lat, pincode_lon),
        "suggested_outlet": best_candidate['name'],
        "outlet_state": best_candidate.get('state', 'Unknown'),
        "outlet_coords": (best_candidate['lat'], best_candidate['lon']),
        "road_distance_km": round(road_km, 2),
        "aerial_distance_km": round(best_candidate['aerial_km'], 2),
        "outlet_radius_km": radius,
        "is_covered": is_covered,
        "coverage_status": status,
        "distance_diff_km": round(road_km - radius, 2),
        "route_coords": route_coords
    }


def find_all_outlets_by_distance(pincode, valid_outlets, geolocator, cache_dict):
    """
    Returns ALL outlets sorted by aerial distance from the given pincode,
    with road distances fetched via OSRM for the closest 10 (rest use aerial estimate).
    """
    clean_pc = clean_pincode_str(pincode)
    coords, err = geocode_pincode(clean_pc, geolocator, cache_dict)

    if not coords:
        return {"found": False, "pincode": clean_pc, "error": sanitize_error_msg(err or "Location Not Found")}

    pincode_lat, pincode_lon = coords

    # Compute aerial distance to every outlet and sort
    candidates = []
    for o in valid_outlets:
        try:
            o_lat, o_lon = float(o['lat']), float(o['lon'])
            a_dist = geodesic((pincode_lat, pincode_lon), (o_lat, o_lon)).km
            candidates.append({**o, 'lat': o_lat, 'lon': o_lon, 'aerial_km': round(a_dist, 2)})
        except Exception:
            continue

    if not candidates:
        return {"found": False, "pincode": clean_pc, "error": "No valid outlets available"}

    candidates.sort(key=lambda x: x['aerial_km'])

    # Fetch road distance via OSRM for top 10; estimate the rest
    results = []
    for idx, c in enumerate(candidates):
        road_km = None
        if idx < 10:  # Only call OSRM for top 10 to keep it fast
            try:
                url = (
                    f"http://router.project-osrm.org/route/v1/driving/"
                    f"{c['lon']},{c['lat']};{pincode_lon},{pincode_lat}"
                    f"?overview=false"
                )
                res = safe_requests_get(url, timeout=6)
                if res and res.status_code == 200:
                    data = res.json()
                    if data.get('code') == 'Ok':
                        road_km = round(data['routes'][0]['distance'] / 1000.0, 2)
            except Exception:
                pass

        if road_km is None:
            road_km = round(c['aerial_km'] * 1.25, 2)  # Fallback estimate

        radius = float(c.get('radius_km', 60.0))
        results.append({
            "rank": idx + 1,
            "name": c['name'],
            "state": c.get('state', 'Unknown'),
            "aerial_km": c['aerial_km'],
            "road_km": road_km,
            "radius_km": radius,
            "is_covered": road_km <= radius,
            "road_is_estimate": idx >= 10,
            "lat": c['lat'],
            "lon": c['lon'],
        })

    return {
        "found": True,
        "pincode": clean_pc,
        "pincode_coords": (pincode_lat, pincode_lon),
        "nearest_outlets": results,
    }


# ----------------- LOAD OUTLETS ----------------- #

with st.spinner("Fetching outlet data from Google Sheets / local cache..."):
    outlets_data, fetch_messages = fetch_outlets_from_gsheet(SHEET_ID)
    st.session_state.outlets = outlets_data

for msg_type, msg_text in fetch_messages:
    if msg_type == "error":
        st.sidebar.error(msg_text)
    elif msg_type == "info":
        st.sidebar.info(msg_text)
    else:
        st.sidebar.warning(msg_text)

col_sb1, col_sb2 = st.sidebar.columns(2)
if col_sb1.button("🔄 Refresh Outlets", use_container_width=True):
    fetch_outlets_from_gsheet.clear()
    st.rerun()
if col_sb2.button("🧹 Start Fresh", use_container_width=True):
    st.session_state.distance_result = None
    st.session_state.batch_result_df = None
    st.session_state.suggested_outlets_df = None
    st.session_state.nearest4_result = None
    st.session_state.nearest_bulk_df = None
    st.rerun()

valid_outlets = [o for o in st.session_state.outlets if pd.notna(o.get('lat')) and pd.notna(o.get('lon'))]
outlet_names = [o['name'] for o in valid_outlets] if valid_outlets else []

# ----------------- SIDEBAR LOGISTICS TOOLS (ALL 3 TOOLS ACCESSIBLE VIA TABS) ----------------- #

st.sidebar.markdown("---")
st.sidebar.markdown("### 🛠️ Logistics & Routing Tools")

tab_suggested, tab_single, tab_batch, tab_nearest4 = st.sidebar.tabs([
    "🎯 Suggested Outlet",
    "📍 Distance Calculator",
    "📦 Batch Distance",
    "📌 Nearest 4"
])

# ==================== TAB 1: SUGGESTED OUTLET (NEAREST OUTLET) ====================
with tab_suggested:
    st.markdown("#### 🎯 Suggested Outlet Finder")
    st.caption("Find the closest outlet in your network for an uploaded Excel list of pincodes or a single pincode.")
    
    suggest_method = st.radio(
        "Input Mode",
        ["📁 Excel / CSV File", "🔍 Single Pincode"],
        index=0,
        key="suggest_input_method_radio"
    )
    
    if suggest_method == "📁 Excel / CSV File":
        sug_file = st.file_uploader(
            "Upload Excel (.xlsx, .xls) or CSV with Pincodes",
            type=["xlsx", "xls", "csv"],
            key="suggested_outlet_file_uploader_tab"
        )
        
        if sug_file is not None:
            try:
                if sug_file.name.endswith(".csv"):
                    df_upload = pd.read_csv(sug_file)
                else:
                    df_upload = pd.read_excel(sug_file)
                    
                st.write(f"📄 Loaded **{len(df_upload)}** rows.")
                
                # Auto-detect Pincode Column
                pincode_col_guess = None
                for col in df_upload.columns:
                    col_l = str(col).lower()
                    if any(k in col_l for k in ['pincode', 'pin code', 'pin', 'postal', 'zip', 'zipcode']):
                        pincode_col_guess = col
                        break
                if not pincode_col_guess:
                    pincode_col_guess = df_upload.columns[0]
                    
                selected_pin_col = st.selectbox(
                    "Select Pincode Column",
                    list(df_upload.columns),
                    index=list(df_upload.columns).index(pincode_col_guess),
                    key="suggest_pin_col_select"
                )
                
                if st.button("🚀 Find Suggested Outlets", use_container_width=True, type="primary", key="btn_run_suggested_batch"):
                    if not valid_outlets:
                        st.error("No valid outlets loaded.")
                    else:
                        geolocator = Nominatim(user_agent="gau_sampurna_suggested_outlets_tab", timeout=10)
                        results = []
                        total_rows = len(df_upload)
                        
                        prog_bar = st.progress(0, text="Matching nearest outlets...")
                        status_placeholder = st.empty()
                        
                        start_time = time.time()
                        for idx, row in df_upload.iterrows():
                            pin_val = row[selected_pin_col]
                            clean_pin = clean_pincode_str(pin_val)
                            
                            status_placeholder.text(f"Row {idx+1}/{total_rows} (PIN: {clean_pin})...")
                            res = find_nearest_outlet_for_pincode(
                                pincode=clean_pin,
                                valid_outlets=valid_outlets,
                                geolocator=geolocator,
                                cache_dict=st.session_state.pincode_cache
                            )
                            results.append(res)
                            prog_bar.progress((idx + 1) / total_rows, text=f"Processed {idx+1}/{total_rows}")
                            
                            if (idx + 1) % 5 == 0 or (idx + 1) == total_rows:
                                temp_df = df_upload.iloc[:idx+1].copy()
                                temp_df['Suggested_Outlet'] = [r.get('suggested_outlet', 'Not Found') if r.get('found') else 'Not Found' for r in results[:idx+1]]
                                temp_df['Outlet_State'] = [r.get('outlet_state', '-') if r.get('found') else '-' for r in results[:idx+1]]
                                temp_df['Road_Distance_km'] = [r.get('road_distance_km', None) if r.get('found') else None for r in results[:idx+1]]
                                temp_df['Coverage_Status'] = [r.get('coverage_status', sanitize_error_msg(r.get('error', 'Error'))) for r in results[:idx+1]]
                                temp_df['Outlet_Radius_km'] = [r.get('outlet_radius_km', None) if r.get('found') else None for r in results[:idx+1]]
                                temp_df['Distance_Diff_km'] = [r.get('distance_diff_km', None) if r.get('found') else None for r in results[:idx+1]]
                                temp_df['Aerial_Distance_km'] = [r.get('aerial_distance_km', None) if r.get('found') else None for r in results[:idx+1]]
                                temp_df['Pincode_Lat'] = [r.get('pincode_coords', (None, None))[0] if r.get('found') else None for r in results[:idx+1]]
                                temp_df['Pincode_Lon'] = [r.get('pincode_coords', (None, None))[1] if r.get('found') else None for r in results[:idx+1]]
                                save_checkpoint(SUGGESTED_CHECKPOINT_PATH, temp_df)

                            if clean_pin not in st.session_state.pincode_cache:
                                time.sleep(0.2)
                                
                        elapsed = time.time() - start_time
                        
                        # Build output DataFrame
                        df_result = df_upload.copy()
                        df_result['Suggested_Outlet'] = [r.get('suggested_outlet', 'Not Found') if r.get('found') else 'Not Found' for r in results]
                        df_result['Outlet_State'] = [r.get('outlet_state', '-') if r.get('found') else '-' for r in results]
                        df_result['Road_Distance_km'] = [r.get('road_distance_km', None) if r.get('found') else None for r in results]
                        df_result['Coverage_Status'] = [r.get('coverage_status', sanitize_error_msg(r.get('error', 'Error'))) for r in results]
                        df_result['Outlet_Radius_km'] = [r.get('outlet_radius_km', None) if r.get('found') else None for r in results]
                        df_result['Distance_Diff_km'] = [r.get('distance_diff_km', None) if r.get('found') else None for r in results]
                        df_result['Aerial_Distance_km'] = [r.get('aerial_distance_km', None) if r.get('found') else None for r in results]
                        df_result['Pincode_Lat'] = [r.get('pincode_coords', (None, None))[0] if r.get('found') else None for r in results]
                        df_result['Pincode_Lon'] = [r.get('pincode_coords', (None, None))[1] if r.get('found') else None for r in results]
                        
                        st.session_state.suggested_outlets_df = df_result
                        save_checkpoint(SUGGESTED_CHECKPOINT_PATH, df_result)
                        st.success(f"✅ Processed {total_rows} pincodes in {elapsed:.1f}s!")
                        st.rerun()
            except Exception as e:
                st.error(f"Error reading file: {e}")
                
    else: # Single Pincode Quick Lookup
        single_pin = st.text_input("Enter Pincode (e.g., 560001)", key="single_pin_suggest_tab")
        if st.button("🔍 Find Nearest Outlet", use_container_width=True, key="btn_single_pin_suggest"):
            if single_pin:
                with st.spinner("Finding nearest outlet..."):
                    geolocator = Nominatim(user_agent="gau_sampurna_single_suggest_tab", timeout=10)
                    res = find_nearest_outlet_for_pincode(
                        pincode=single_pin,
                        valid_outlets=valid_outlets,
                        geolocator=geolocator,
                        cache_dict=st.session_state.pincode_cache
                    )
                    if res.get("found"):
                        st.session_state.distance_result = {
                            "pincode": res["pincode"],
                            "distance": res["road_distance_km"],
                            "pincode_coords": res["pincode_coords"],
                            "outlet_name": res["suggested_outlet"],
                            "outlet_coords": res["outlet_coords"],
                            "route_coords": res.get("route_coords", []),
                            "is_suggested": True,
                            "coverage_status": res["coverage_status"],
                            "radius_km": res["outlet_radius_km"],
                            "outlet_state": res["outlet_state"]
                        }
                        st.success(f"🎯 Nearest Outlet: **{res['suggested_outlet']}** ({res['outlet_state']})")
                        st.info(f"🛣️ Road Distance: **{res['road_distance_km']:.2f} km** (Radius: {res['outlet_radius_km']} km)")
                        st.rerun()
                    else:
                        st.error(f"Could not find nearest outlet: {res.get('error')}")
            else:
                st.warning("Please enter a pincode.")

# ==================== TAB 2: SINGLE DISTANCE CALCULATOR ====================
with tab_single:
    st.markdown("#### 📍 Single Distance Calculator")
    st.caption("Calculate the exact driving road route and distance between a specific outlet and a pincode.")
    
    if valid_outlets:
        selected_outlet_name = st.selectbox("Select Target Outlet", outlet_names, key="single_dist_outlet_select")
        pincode_input = st.text_input("Enter Pincode (e.g., 110001)", key="single_dist_pincode_input")
        
        col_calc1, col_calc2 = st.columns(2)
        if col_calc1.button("Calculate Distance", use_container_width=True, type="primary", key="btn_calc_single_dist"):
            if pincode_input:
                try:
                    with st.spinner("Calculating driving route..."):
                        geolocator = Nominatim(user_agent="gau_sampurna_single_dist", timeout=10)
                        location = geolocator.geocode(f"{pincode_input}, India", timeout=10)
                        
                        if location:
                            selected_outlet = next(o for o in valid_outlets if o['name'] == selected_outlet_name)
                            outlet_coords = (selected_outlet['lat'], selected_outlet['lon'])
                            pincode_coords = (location.latitude, location.longitude)
                            
                            url = f"http://router.project-osrm.org/route/v1/driving/{outlet_coords[1]},{outlet_coords[0]};{pincode_coords[1]},{pincode_coords[0]}?overview=full&geometries=geojson"
                            res = safe_requests_get(url, timeout=6)
                            
                            if res and res.status_code == 200:
                                data = res.json()
                                if data.get('code') == 'Ok':
                                    route = data['routes'][0]
                                    distance_km = route['distance'] / 1000.0
                                    geometry = route['geometry']['coordinates']
                                    route_coords = [(lat, lon) for lon, lat in geometry]
                                    
                                    st.session_state.distance_result = {
                                        "pincode": pincode_input,
                                        "distance": distance_km,
                                        "pincode_coords": pincode_coords,
                                        "outlet_name": selected_outlet_name,
                                        "outlet_coords": outlet_coords,
                                        "route_coords": route_coords,
                                        "is_suggested": False,
                                        "radius_km": selected_outlet.get('radius_km', 60.0),
                                        "outlet_state": selected_outlet.get('state', 'Unknown')
                                    }
                                    st.success(f"🛣️ Road Distance: **{distance_km:.2f} km**")
                                    st.rerun()
                                else:
                                    st.error("Could not find a driving road route.")
                            else:
                                st.error("Routing service unavailable.")
                        else:
                            st.error("Location not found for this pincode.")
                except Exception as e:
                    st.error(f"Error: {e}")
            else:
                st.warning("Please enter a pincode.")
                
        if col_calc2.button("Reset Route", use_container_width=True, key="btn_reset_single_dist"):
            st.session_state.distance_result = None
            st.rerun()
            
        if st.session_state.distance_result and not st.session_state.distance_result.get("is_suggested"):
            res_curr = st.session_state.distance_result
            st.markdown(
                f"""
                <div style="background-color: #f1f8e9; border: 1px solid #c8e6c9; padding: 10px; border-radius: 6px; margin-top: 10px;">
                    <div style="font-weight: 600; color: #2e7d32;">📍 Active Route:</div>
                    <div style="font-size: 13px; color: #333;">Outlet: <b>{res_curr['outlet_name']}</b></div>
                    <div style="font-size: 13px; color: #333;">Pincode: <b>{res_curr['pincode']}</b></div>
                    <div style="font-size: 14px; font-weight: 700; color: #1b5e20; margin-top: 4px;">Road Distance: {res_curr['distance']:.2f} km</div>
                </div>
                """,
                unsafe_allow_html=True
            )
    else:
        st.info("No outlets available.")

# ==================== TAB 3: BATCH DISTANCE CALCULATOR (FIXED OUTLET) ====================
with tab_batch:
    st.markdown("#### 📦 Batch Distance Calculator (Fixed Outlet)")
    st.caption("Upload a list of pincodes and calculate road distances from all of them to one specific target outlet.")
    
    if valid_outlets:
        batch_outlet_name = st.selectbox("Select Target Outlet", outlet_names, key="batch_target_outlet_select_tab")
        batch_file = st.file_uploader("Upload CSV or Excel with Pincodes", type=["csv", "xlsx", "xls"], key="batch_file_uploader_tab")
        
        if batch_file and st.button("Calculate Batch Distances", use_container_width=True, type="primary", key="btn_run_batch_fixed"):
            try:
                if batch_file.name.endswith('.csv'):
                    df_batch = pd.read_csv(batch_file)
                else:
                    df_batch = pd.read_excel(batch_file)
                    
                pincode_col = None
                for col in df_batch.columns:
                    if any(k in str(col).lower() for k in ['pincode', 'pin', 'zip', 'postal']):
                        pincode_col = col
                        break
                if not pincode_col:
                    pincode_col = df_batch.columns[0]
                    
                selected_outlet = next(o for o in valid_outlets if o['name'] == batch_outlet_name)
                outlet_coords = (selected_outlet['lat'], selected_outlet['lon'])
                
                distances = []
                geolocator = Nominatim(user_agent="outlet_coverage_batch_fixed_tab", timeout=10)
                
                total_rows = len(df_batch)
                my_bar = st.progress(0, text="Processing pincodes...")
                
                for idx, row in df_batch.iterrows():
                    pc = clean_pincode_str(row[pincode_col])
                    dist = None
                    if pc:
                        coords, _ = geocode_pincode(pc, geolocator, st.session_state.pincode_cache)
                        if coords:
                            try:
                                url = f"http://router.project-osrm.org/route/v1/driving/{outlet_coords[1]},{outlet_coords[0]};{coords[1]},{coords[0]}?overview=false"
                                res = safe_requests_get(url, timeout=5)
                                if res and res.status_code == 200:
                                    data = res.json()
                                    if data.get('code') == 'Ok':
                                        dist = round(data['routes'][0]['distance'] / 1000.0, 2)
                            except Exception:
                                pass
                    distances.append(dist)
                    my_bar.progress((idx + 1) / total_rows, text=f"Processed {idx+1}/{total_rows}")
                    
                    if (idx + 1) % 5 == 0 or (idx + 1) == total_rows:
                        temp_b_df = df_batch.iloc[:idx+1].copy()
                        temp_b_df['Distance_km'] = distances
                        temp_b_df['Target_Outlet'] = batch_outlet_name
                        save_checkpoint(BATCH_FIXED_CHECKPOINT_PATH, temp_b_df)
                    
                    if pc not in st.session_state.pincode_cache:
                        time.sleep(0.4)
                    
                df_batch['Distance_km'] = distances
                df_batch['Target_Outlet'] = batch_outlet_name
                st.session_state.batch_result_df = df_batch
                save_checkpoint(BATCH_FIXED_CHECKPOINT_PATH, df_batch)
                st.success("✅ Batch calculation complete!")
                st.rerun()
            except Exception as e:
                st.error(f"Error processing batch: {e}")
                
        # Sidebar download options for Batch Result
        if st.session_state.batch_result_df is not None:
            st.markdown("---")
            st.markdown("**📥 Download Batch Distances:**")
            
            # CSV Download
            csv_batch = st.session_state.batch_result_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📄 Download CSV",
                data=csv_batch,
                file_name=f"batch_distances_{batch_outlet_name}.csv",
                mime="text/csv",
                use_container_width=True,
                key="sidebar_dl_batch_csv"
            )
            
            # Excel Download
            ex_batch_buf = io.BytesIO()
            with pd.ExcelWriter(ex_batch_buf, engine='openpyxl') as writer:
                st.session_state.batch_result_df.to_excel(writer, index=False, sheet_name="Batch_Distances")
            st.download_button(
                label="📊 Download Excel (.xlsx)",
                data=ex_batch_buf.getvalue(),
                file_name=f"batch_distances_{batch_outlet_name}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key="sidebar_dl_batch_excel"
            )
    else:
        st.info("No outlets available.")

# ==================== TAB 4: NEAREST OUTLETS BY PINCODE (SINGLE + BULK) ====================
with tab_nearest4:
    st.markdown("#### 📌 Nearest Outlets by Pincode")

    n4_mode = st.radio(
        "Mode",
        ["🔍 Single Pincode", "📤 Bulk Upload (CSV / Excel)"],
        horizontal=True,
        key="n4_mode_radio"
    )

    # ---- SINGLE PINCODE MODE ----
    if n4_mode == "🔍 Single Pincode":
        st.caption("Enter a pincode — all outlets ranked by distance will appear below.")
        n4_pincode = st.text_input("Pincode (e.g., 560001)", key="n4_pincode_input")
        col_n4a, col_n4b = st.columns(2)

        if col_n4a.button("🔍 Find Nearest", use_container_width=True, type="primary", key="btn_find_nearest4"):
            if n4_pincode.strip():
                with st.spinner("Ranking all outlets..."):
                    geolocator = Nominatim(user_agent="gau_sampurna_nearest_single", timeout=10)
                    n4_res = find_all_outlets_by_distance(
                        pincode=n4_pincode.strip(),
                        valid_outlets=valid_outlets,
                        geolocator=geolocator,
                        cache_dict=st.session_state.pincode_cache,
                    )
                    st.session_state.nearest4_result = n4_res
                    st.session_state.nearest_bulk_df = None
                    st.rerun()
            else:
                st.warning("Please enter a pincode.")

        if col_n4b.button("🧹 Clear", use_container_width=True, key="btn_clear_nearest4"):
            st.session_state.nearest4_result = None
            st.rerun()

        # Show ALL results in a scrollable sidebar container
        n4_preview = st.session_state.nearest4_result
        if n4_preview:
            if n4_preview.get("found"):
                all_outlets = n4_preview["nearest_outlets"]
                covered_cnt = sum(1 for o in all_outlets if o["is_covered"])
                st.success(f"✅ PIN **{n4_preview['pincode']}** — {len(all_outlets)} outlets · {covered_cnt} covered")
                # Scrollable container with all results
                with st.container(height=520):
                    for o in all_outlets:
                        status_icon = "✅" if o["is_covered"] else "⚠️"
                        border_clr = "#2e7d32" if o["is_covered"] else "#f57f17"
                        bg_clr = "#f1f8e9" if o["is_covered"] else "#fffde7"
                        est_note = " <span style='font-size:10px;color:#aaa;'>(est.)</span>" if o.get("road_is_estimate") else ""
                        st.markdown(
                            f"""
                            <div style="background:{bg_clr};border-left:4px solid {border_clr};
                            padding:8px 10px;border-radius:6px;margin-bottom:5px;">
                            <span style="font-size:13px;font-weight:700;color:#1a1a1a;">#{o['rank']} {o['name']}</span><br/>
                            <span style="font-size:11px;color:#666;">{o['state']}</span><br/>
                            <span style="font-size:12px;">🛣️ <b>{o['road_km']} km</b>{est_note} &nbsp;·&nbsp; 🛩️ {o['aerial_km']} km</span><br/>
                            <span style="font-size:11px;">{status_icon} {'Covered' if o['is_covered'] else 'Out of Radius'} &nbsp;·&nbsp; Radius: {o['radius_km']} km</span>
                            </div>
                            """,
                            unsafe_allow_html=True
                        )
            else:
                st.error(f"❌ {n4_preview.get('error', 'Pincode not found')}")

    # ---- BULK UPLOAD MODE ----
    else:
        st.caption("Upload a CSV or Excel file with a column of pincodes. For each pincode, the nearest outlet and distance will be found.")
        n4_bulk_file = st.file_uploader(
            "Upload CSV / Excel with Pincodes",
            type=["csv", "xlsx", "xls"],
            key="n4_bulk_file_uploader"
        )

        if n4_bulk_file:
            try:
                if n4_bulk_file.name.endswith(".csv"):
                    df_bulk_up = pd.read_csv(n4_bulk_file)
                else:
                    df_bulk_up = pd.read_excel(n4_bulk_file)

                st.write(f"📄 **{len(df_bulk_up)}** rows loaded")

                # Auto-detect pincode column
                pc_col_guess = None
                for c in df_bulk_up.columns:
                    if any(k in str(c).lower() for k in ['pincode', 'pin', 'postal', 'zip']):
                        pc_col_guess = c
                        break
                if not pc_col_guess:
                    pc_col_guess = df_bulk_up.columns[0]

                bulk_pc_col = st.selectbox(
                    "Select Pincode Column",
                    list(df_bulk_up.columns),
                    index=list(df_bulk_up.columns).index(pc_col_guess),
                    key="n4_bulk_pc_col"
                )

                col_rb1, col_rb2 = st.columns(2)
                if col_rb1.button("🚀 Find Nearest for All", use_container_width=True, type="primary", key="btn_bulk_nearest_run"):
                    if not valid_outlets:
                        st.error("No outlets loaded.")
                    else:
                        geolocator = Nominatim(user_agent="gau_sampurna_nearest_bulk", timeout=10)
                        bulk_results = []
                        total_bulk = len(df_bulk_up)
                        prog = st.progress(0, text="Processing pincodes...")
                        status_ph = st.empty()

                        for idx, row in df_bulk_up.iterrows():
                            pin_val = clean_pincode_str(row[bulk_pc_col])
                            status_ph.text(f"Row {idx+1}/{total_bulk}: PIN {pin_val}")
                            res = find_all_outlets_by_distance(
                                pincode=pin_val,
                                valid_outlets=valid_outlets,
                                geolocator=geolocator,
                                cache_dict=st.session_state.pincode_cache,
                            )
                            if res.get("found") and res["nearest_outlets"]:
                                nearest = res["nearest_outlets"][0]
                                bulk_results.append({
                                    "Pincode": res["pincode"],
                                    "Nearest Outlet": nearest["name"],
                                    "State": nearest["state"],
                                    "Road Distance (km)": nearest["road_km"],
                                    "Aerial (km)": nearest["aerial_km"],
                                    "Radius (km)": nearest["radius_km"],
                                    "Est. Road Dist": "Yes" if nearest.get("road_is_estimate") else "No",
                                    "Status": "✅ Covered" if nearest["is_covered"] else "⚠️ Out of Radius",
                                    "#2 Outlet": res["nearest_outlets"][1]["name"] if len(res["nearest_outlets"]) > 1 else "-",
                                    "#2 Road km": res["nearest_outlets"][1]["road_km"] if len(res["nearest_outlets"]) > 1 else "-",
                                    "#3 Outlet": res["nearest_outlets"][2]["name"] if len(res["nearest_outlets"]) > 2 else "-",
                                    "#3 Road km": res["nearest_outlets"][2]["road_km"] if len(res["nearest_outlets"]) > 2 else "-",
                                })
                            else:
                                bulk_results.append({
                                    "Pincode": pin_val,
                                    "Nearest Outlet": "Not Found",
                                    "State": "-",
                                    "Road Distance (km)": None,
                                    "Aerial (km)": None,
                                    "Radius (km)": None,
                                    "Est. Road Dist": "-",
                                    "Status": sanitize_error_msg(res.get("error", "Error")),
                                    "#2 Outlet": "-", "#2 Road km": "-",
                                    "#3 Outlet": "-", "#3 Road km": "-",
                                })
                            prog.progress((idx + 1) / total_bulk, text=f"{idx+1}/{total_bulk} processed")
                            
                            if (idx + 1) % 5 == 0 or (idx + 1) == total_bulk:
                                save_checkpoint(NEAREST_BULK_CHECKPOINT_PATH, pd.DataFrame(bulk_results))

                            if pin_val not in st.session_state.pincode_cache:
                                time.sleep(0.4)

                        status_ph.empty()
                        df_bulk_res = pd.DataFrame(bulk_results)
                        st.session_state.nearest_bulk_df = df_bulk_res
                        save_checkpoint(NEAREST_BULK_CHECKPOINT_PATH, df_bulk_res)
                        st.session_state.nearest4_result = None
                        st.success(f"✅ Done! {total_bulk} pincodes processed.")
                        st.rerun()

                if col_rb2.button("🧹 Clear Bulk", use_container_width=True, key="btn_clear_bulk_nearest"):
                    st.session_state.nearest_bulk_df = None
                    clear_checkpoint(NEAREST_BULK_CHECKPOINT_PATH)
                    st.rerun()

            except Exception as e:
                st.error(f"Error reading file: {e}")

        # Show bulk results summary in sidebar
        if st.session_state.nearest_bulk_df is not None:
            df_b = st.session_state.nearest_bulk_df
            covered_b = len(df_b[df_b["Status"].str.contains("Covered", na=False)])
            st.success(f"✅ {len(df_b)} pincodes · {covered_b} covered — Full table on main screen ↓")
            with st.container(height=300):
                for _, br in df_b.iterrows():
                    is_cov = "✅" in str(br.get("Status", ""))
                    bc = "#2e7d32" if is_cov else "#f57f17"
                    bg = "#f1f8e9" if is_cov else "#fffde7"
                    st.markdown(
                        f"""
                        <div style="background:{bg};border-left:4px solid {bc};
                        padding:6px 9px;border-radius:5px;margin-bottom:4px;">
                        <b>{br['Pincode']}</b> → {br['Nearest Outlet']}<br/>
                        <span style="font-size:11px;color:#555;">🛣️ {br['Road Distance (km)']} km &nbsp;·&nbsp; {br['Status']}</span>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

# ----------------- MAIN SCREEN CONTENT ----------------- #

# 0. NEAREST OUTLETS RESULTS SECTION (All outlets ranked by distance)
n4_main = st.session_state.nearest4_result
if n4_main and n4_main.get("found"):
    outlets_list = n4_main["nearest_outlets"]
    total_n4 = len(outlets_list)
    covered_n4 = sum(1 for o in outlets_list if o["is_covered"])
    nearest = outlets_list[0] if outlets_list else None

    st.markdown("---")

    # Header row
    hdr_col1, hdr_col2 = st.columns([3, 1])
    with hdr_col1:
        st.markdown(f"### 📌 All Outlets Ranked by Distance — Pincode **{n4_main['pincode']}**")
        st.caption(
            f"📍 {n4_main['pincode_coords'][0]:.5f}, {n4_main['pincode_coords'][1]:.5f} "
            f"· {total_n4} outlets · {covered_n4} within coverage radius "
            f"· Road distances for top 10 are exact (OSRM), rest are estimates"
        )
    with hdr_col2:
        if st.button("🧹 Clear Results", key="btn_clear_n4_main", use_container_width=True):
            st.session_state.nearest4_result = None
            st.rerun()

    # Top-3 highlight cards
    if nearest:
        top3 = outlets_list[:3]
        card_cols = st.columns(len(top3))
        medal = ["🥇", "🥈", "🥉"]
        for ci, (cc, o) in enumerate(zip(card_cols, top3)):
            covered = o["is_covered"]
            border = "#1b5e20" if covered else "#e65100"
            bg = "#f1f8e9" if covered else "#fff3e0"
            badge_bg = "#c8e6c9" if covered else "#ffe0b2"
            badge_fg = "#1b5e20" if covered else "#bf360c"
            badge_txt = "✅ Covered" if covered else "⚠️ Out of Radius"
            est_note = "<span style='font-size:10px;color:#999;'> (estimated)</span>" if o.get("road_is_estimate") else ""
            with cc:
                st.markdown(
                    f"""
                    <div style="background:{bg};border:2px solid {border};
                    border-radius:12px;padding:16px 14px;text-align:center;
                    box-shadow:0 3px 10px rgba(0,0,0,0.1);margin-bottom:12px;">
                      <div style="font-size:28px;margin-bottom:2px;">{medal[ci]}</div>
                      <div style="font-size:14px;font-weight:800;color:#1a1a1a;
                      line-height:1.3;margin-bottom:3px;">{o['name']}</div>
                      <div style="font-size:12px;color:#666;margin-bottom:10px;">{o['state']}</div>
                      <div style="font-size:28px;font-weight:900;color:{border};
                      margin-bottom:1px;">{o['road_km']} km</div>
                      <div style="font-size:11px;color:#888;margin-bottom:4px;">🛣️ Road Distance{est_note}</div>
                      <div style="font-size:12px;color:#555;margin-bottom:10px;">🛩️ Aerial: {o['aerial_km']} km</div>
                      <span style="background:{badge_bg};color:{badge_fg};font-size:11px;
                      font-weight:700;padding:4px 12px;border-radius:20px;">{badge_txt}</span>
                      <div style="font-size:11px;color:#999;margin-top:6px;">Radius: {o['radius_km']} km</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

    # Full ranked table — all outlets
    st.markdown("#### 📋 All Outlets — Ranked by Distance from Pincode")
    df_n4 = pd.DataFrame([
        {
            "#": o["rank"],
            "Outlet Name": o["name"],
            "State": o["state"],
            "Road Distance (km)": o["road_km"],
            "Aerial (km)": o["aerial_km"],
            "Radius (km)": o["radius_km"],
            "Est.": "Yes" if o.get("road_is_estimate") else "No",
            "Status": "✅ Covered" if o["is_covered"] else "⚠️ Out of Radius"
        }
        for o in outlets_list
    ])

    # Colour rows by status
    def _colour_status(val):
        if "Covered" in str(val):
            return "background-color: #e8f5e9; color: #1b5e20; font-weight: 600;"
        return "background-color: #fff3e0; color: #bf360c; font-weight: 600;"

    if hasattr(df_n4.style, "map"):
        styled_df = df_n4.style.map(_colour_status, subset=["Status"])
    else:
        styled_df = df_n4.style.applymap(_colour_status, subset=["Status"])
    st.dataframe(styled_df, use_container_width=True, hide_index=True, height=420)

    # Download buttons
    dl1, dl2 = st.columns(2)
    with dl1:
        csv_n4 = df_n4.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="📥 Download CSV",
            data=csv_n4,
            file_name=f"outlets_by_distance_{n4_main['pincode']}.csv",
            mime="text/csv",
            use_container_width=True,
            key="dl_n4_csv"
        )
    with dl2:
        ex_n4_buf = io.BytesIO()
        with pd.ExcelWriter(ex_n4_buf, engine='openpyxl') as writer:
            df_n4.to_excel(writer, index=False, sheet_name="Outlets_By_Distance")
        st.download_button(
            label="📊 Download Excel",
            data=ex_n4_buf.getvalue(),
            file_name=f"outlets_by_distance_{n4_main['pincode']}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="dl_n4_xlsx"
        )

elif n4_main and not n4_main.get("found"):
    st.markdown("---")
    st.error(f"📌 Nearest Outlets — ❌ {n4_main.get('error', 'Pincode not found')}")

# BULK NEAREST OUTLETS RESULTS SECTION
bulk_n4 = st.session_state.nearest_bulk_df
if bulk_n4 is not None and not bulk_n4.empty:
    st.markdown("---")
    total_b = len(bulk_n4)
    covered_b = len(bulk_n4[bulk_n4["Status"].str.contains("Covered", na=False)])
    out_b = total_b - covered_b

    hb1, hb2 = st.columns([3, 1])
    with hb1:
        st.markdown(f"### 📤 Bulk Nearest Outlet Results — {total_b} Pincodes")
        st.caption(f"✅ {covered_b} covered · ⚠️ {out_b} out of radius · Columns #2 and #3 show 2nd & 3rd nearest outlets")
    with hb2:
        if st.button("🧹 Clear Bulk", key="btn_clear_bulk_main", use_container_width=True):
            st.session_state.nearest_bulk_df = None
            clear_checkpoint(NEAREST_BULK_CHECKPOINT_PATH)
            st.rerun()

    # KPI strip
    bkpi1, bkpi2, bkpi3 = st.columns(3)
    bkpi1.metric("📦 Pincodes Processed", total_b)
    bkpi2.metric("✅ Covered", covered_b, f"{covered_b/total_b*100:.1f}%")
    bkpi3.metric("⚠️ Out of Radius", out_b, f"{out_b/total_b*100:.1f}%", delta_color="inverse")

    # Colour rows
    def _bulk_colour(val):
        if "✅" in str(val):
            return "background-color: #e8f5e9; color: #1b5e20; font-weight:600;"
        if "⚠️" in str(val) or "Out" in str(val):
            return "background-color: #fff3e0; color: #bf360c; font-weight:600;"
        return ""

    if hasattr(bulk_n4.style, "map"):
        styled_bulk = bulk_n4.style.map(_bulk_colour, subset=["Status"])
    else:
        styled_bulk = bulk_n4.style.applymap(_bulk_colour, subset=["Status"])
    st.dataframe(styled_bulk, use_container_width=True, hide_index=True, height=420)

    dl_b1, dl_b2 = st.columns(2)
    with dl_b1:
        csv_bulk = bulk_n4.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Download CSV", csv_bulk,
            file_name="bulk_nearest_outlets.csv", mime="text/csv",
            use_container_width=True, key="dl_bulk_n4_csv"
        )
    with dl_b2:
        ex_b = io.BytesIO()
        with pd.ExcelWriter(ex_b, engine='openpyxl') as wr:
            bulk_n4.to_excel(wr, index=False, sheet_name="Bulk_Nearest")
        st.download_button(
            "📊 Download Excel", ex_b.getvalue(),
            file_name="bulk_nearest_outlets.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True, key="dl_bulk_n4_xlsx"
        )

# 1. Top KPI Summary Cards
kpi_c1, kpi_c2, kpi_c3, kpi_c4 = st.columns(4)
unique_states = len(set(o.get('state', '') for o in valid_outlets))
avg_radius = sum(float(o.get('radius_km', 60)) for o in valid_outlets) / len(valid_outlets) if valid_outlets else 0

with kpi_c1:
    st.metric("🏢 Total Outlets", f"{len(valid_outlets)}")
with kpi_c2:
    st.metric("📍 States Covered", f"{unique_states}")
with kpi_c3:
    st.metric("🎯 Avg Coverage Radius", f"{avg_radius:.1f} km")
with kpi_c4:
    cached_pins = len(st.session_state.pincode_cache)
    st.metric("⚡ Cached Pincodes", f"{cached_pins}")

# 2. SUGGESTED OUTLETS RESULTS SECTION (If active)
if st.session_state.suggested_outlets_df is not None and not st.session_state.suggested_outlets_df.empty:
    df_sug = st.session_state.suggested_outlets_df
    
    st.markdown("---")
    st.markdown("### 🎯 Suggested Nearest Outlet Analysis Results")
    
    total_pins = len(df_sug)
    covered_pins = len(df_sug[df_sug['Coverage_Status'] == 'Covered'])
    out_of_radius_pins = len(df_sug[df_sug['Coverage_Status'] == 'Out of Radius'])
    invalid_pins = total_pins - covered_pins - out_of_radius_pins
    coverage_rate = (covered_pins / total_pins * 100.0) if total_pins > 0 else 0
    unique_suggested = df_sug['Suggested_Outlet'].nunique() if 'Suggested_Outlet' in df_sug else 0
    
    # KPI metrics for batch
    sug_kpi1, sug_kpi2, sug_kpi3, sug_kpi4 = st.columns(4)
    with sug_kpi1:
        st.metric("📦 Pincodes Analyzed", f"{total_pins}")
    with sug_kpi2:
        st.metric("✅ Within Coverage Radius", f"{covered_pins}", f"{coverage_rate:.1f}% covered", delta_color="normal")
    with sug_kpi3:
        st.metric("⚠️ Outside Coverage Radius", f"{out_of_radius_pins}", f"{(out_of_radius_pins/total_pins*100):.1f}%", delta_color="inverse")
    with sug_kpi4:
        st.metric("🏬 Outlets Assigned", f"{unique_suggested} outlets")

    # Filters and View Options
    filter_col1, filter_col2, filter_col3 = st.columns([1, 1, 2])
    with filter_col1:
        status_filter = st.selectbox("Filter by Status", ["All", "Covered Only", "Outside Radius Only"], index=0, key="sug_status_filter")
    with filter_col2:
        outlet_options = ["All Outlets"] + sorted([str(x) for x in df_sug['Suggested_Outlet'].unique() if pd.notna(x)])
        outlet_filter = st.selectbox("Filter by Suggested Outlet", outlet_options, index=0, key="sug_outlet_filter")
    with filter_col3:
        pass

    # Filtered dataframe
    df_display = df_sug.copy()
    if status_filter == "Covered Only":
        df_display = df_display[df_display['Coverage_Status'] == 'Covered']
    elif status_filter == "Outside Radius Only":
        df_display = df_display[df_display['Coverage_Status'] == 'Out of Radius']
        
    if outlet_filter != "All Outlets":
        df_display = df_display[df_display['Suggested_Outlet'] == outlet_filter]

    st.dataframe(df_display, use_container_width=True, height=280)

    # Download and Action Buttons
    dl_col1, dl_col2, dl_col3, dl_col4 = st.columns([1, 1, 1, 1])
    
    with dl_col1:
        # Excel download
        excel_buffer = io.BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
            df_sug.to_excel(writer, index=False, sheet_name='Suggested_Outlets')
        st.download_button(
            label="📥 Download Excel (.xlsx)",
            data=excel_buffer.getvalue(),
            file_name="suggested_outlets_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary",
            key="main_dl_suggested_excel"
        )
        
    with dl_col2:
        # CSV download
        csv_data = df_sug.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download CSV (.csv)",
            data=csv_data,
            file_name="suggested_outlets_results.csv",
            mime="text/csv",
            use_container_width=True,
            key="main_dl_suggested_csv"
        )
        
    with dl_col3:
        plot_pincodes = st.checkbox("📍 Show Pincodes on Map", value=True, help="Plot the batch pincodes onto the Folium map below", key="plot_pincodes_checkbox")
        
    with dl_col4:
        if st.button("🧹 Clear Batch Results", use_container_width=True, key="btn_clear_suggested_batch"):
            st.session_state.suggested_outlets_df = None
            clear_checkpoint(SUGGESTED_CHECKPOINT_PATH)
            st.rerun()

else:
    plot_pincodes = False

# 3. BATCH DISTANCE (FIXED OUTLET) RESULTS SECTION (If active)
if st.session_state.batch_result_df is not None and not st.session_state.batch_result_df.empty:
    df_fixed_batch = st.session_state.batch_result_df
    
    st.markdown("---")
    st.markdown("### 📦 Batch Distance (Fixed Outlet) Results")
    
    target_out_name = df_fixed_batch['Target_Outlet'].iloc[0] if 'Target_Outlet' in df_fixed_batch else 'Selected Outlet'
    valid_dists = [d for d in df_fixed_batch['Distance_km'] if pd.notna(d)]
    avg_batch_dist = sum(valid_dists)/len(valid_dists) if valid_dists else 0
    max_batch_dist = max(valid_dists) if valid_dists else 0
    
    b_kpi1, b_kpi2, b_kpi3, b_kpi4 = st.columns(4)
    with b_kpi1:
        st.metric("🏬 Target Outlet", f"{target_out_name}")
    with b_kpi2:
        st.metric("📦 Pincodes Processed", f"{len(df_fixed_batch)}")
    with b_kpi3:
        st.metric("🛣️ Avg Distance", f"{avg_batch_dist:.1f} km")
    with b_kpi4:
        st.metric("📍 Max Distance", f"{max_batch_dist:.1f} km")
        
    st.dataframe(df_fixed_batch, use_container_width=True, height=250)
    
    bdl_c1, bdl_c2, bdl_c3 = st.columns([1, 1, 2])
    with bdl_c1:
        csv_batch_main = df_fixed_batch.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download CSV (.csv)",
            data=csv_batch_main,
            file_name=f"batch_distances_{target_out_name}.csv",
            mime="text/csv",
            use_container_width=True,
            key="main_dl_batch_fixed_csv"
        )
    with bdl_c2:
        ex_batch_buf_main = io.BytesIO()
        with pd.ExcelWriter(ex_batch_buf_main, engine='openpyxl') as writer:
            df_fixed_batch.to_excel(writer, index=False, sheet_name="Batch_Distances")
        st.download_button(
            label="📥 Download Excel (.xlsx)",
            data=ex_batch_buf_main.getvalue(),
            file_name=f"batch_distances_{target_out_name}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="main_dl_batch_fixed_excel"
        )
    with bdl_c3:
        if st.button("🧹 Clear Batch Distance Results", use_container_width=True, key="btn_clear_fixed_batch"):
            st.session_state.batch_result_df = None
            clear_checkpoint(BATCH_FIXED_CHECKPOINT_PATH)
            st.rerun()

# 4. INTERACTIVE FOLIUM MAP
st.markdown("---")
st.subheader("🗺️ Interactive Network & Route Map")

distance_result = st.session_state.distance_result

# Calculate Map Center
if distance_result and "outlet_coords" in distance_result and "pincode_coords" in distance_result:
    avg_lat = (distance_result["outlet_coords"][0] + distance_result["pincode_coords"][0]) / 2
    avg_lon = (distance_result["outlet_coords"][1] + distance_result["pincode_coords"][1]) / 2
elif valid_outlets:
    avg_lat = sum(float(o['lat']) for o in valid_outlets) / len(valid_outlets)
    avg_lon = sum(float(o['lon']) for o in valid_outlets) / len(valid_outlets)
else:
    avg_lat, avg_lon = 20.5937, 78.9629 # Default center (India)

m = folium.Map(location=[avg_lat, avg_lon], zoom_start=6, tiles="OpenStreetMap")

# Render Outlet Markers with Radius Circles
for outlet in valid_outlets:
    tooltip_text = f"🏬 {outlet['name']} ({outlet.get('state', 'India')}) — Radius: {outlet.get('radius_km', 60)} km"
    marker = folium.Marker(
        location=[float(outlet['lat']), float(outlet['lon'])],
        popup=folium.Popup(f"<b>{outlet['name']}</b><br>State: {outlet.get('state', 'N/A')}<br>Coverage Radius: {outlet.get('radius_km', 60)} km", max_width=300),
        tooltip=tooltip_text,
        icon=folium.Icon(icon="industry", prefix="fa", color="blue")
    )
    marker.add_to(m)
    
    circle = folium.Circle(
        location=[float(outlet['lat']), float(outlet['lon'])],
        radius=float(outlet.get('radius_km', 60.0)) * 1000,
        color="#3186cc",
        fill=True,
        fill_color="#3186cc",
        opacity=0,
        fill_opacity=0
    )
    circle.add_to(m)
    
    hover_js = f"""
        {marker.get_name()}.on('mouseover', function(e) {{
            {circle.get_name()}.setStyle({{opacity: 1, fillOpacity: 0.18}});
        }});
        {marker.get_name()}.on('mouseout', function(e) {{
            {circle.get_name()}.setStyle({{opacity: 0, fillOpacity: 0}});
        }});
    """
    m.get_root().script.add_child(folium.Element(hover_js))

# Render Single Distance / Single Suggested Outlet Route
if distance_result:
    folium.Marker(
        location=distance_result["pincode_coords"],
        popup=f"<b>Pincode: {distance_result['pincode']}</b><br>Target/Nearest Outlet: {distance_result.get('outlet_name')}<br>Road Distance: {distance_result['distance']:.2f} km",
        tooltip=f"Pincode: {distance_result['pincode']} ({distance_result['distance']:.2f} km to {distance_result.get('outlet_name')})",
        icon=folium.Icon(icon="map-pin", prefix="fa", color="green" if distance_result.get("is_suggested") else "red")
    ).add_to(m)
    
    if "route_coords" in distance_result and distance_result["route_coords"]:
        folium.PolyLine(
            locations=distance_result["route_coords"],
            color="#2e7d32" if distance_result.get("is_suggested") else "#d32f2f",
            weight=4,
            opacity=0.85,
            tooltip=f"Road Route: {distance_result['distance']:.2f} km"
        ).add_to(m)

# Render Nearest Outlets on Map — top 5 highlighted (if active)
n4_map = st.session_state.nearest4_result
if n4_map and n4_map.get("found"):
    pc_lat, pc_lon = n4_map["pincode_coords"]
    all_near = n4_map["nearest_outlets"]
    top5 = all_near[:5]

    # Pincode marker (purple star)
    folium.Marker(
        location=[pc_lat, pc_lon],
        popup=folium.Popup(
            f"<b>Pincode: {n4_map['pincode']}</b><br>"
            f"Total outlets ranked: {len(all_near)}<br>"
            f"Nearest: {all_near[0]['name']} ({all_near[0]['road_km']} km)",
            max_width=280
        ),
        tooltip=f"📍 Pincode: {n4_map['pincode']} — {len(all_near)} outlets ranked",
        icon=folium.Icon(icon="star", prefix="fa", color="purple")
    ).add_to(m)

    # Top-5 outlet markers with dashed connecting lines
    top5_colors = ["red", "orange", "cadetblue", "lightgray", "darkgreen"]
    for rank, outlet in enumerate(top5):
        status_txt = "✅ Covered" if outlet["is_covered"] else "⚠️ Out of Radius"
        est_note = " (estimated)" if outlet.get("road_is_estimate") else ""
        folium.Marker(
            location=[outlet["lat"], outlet["lon"]],
            popup=folium.Popup(
                f"<b>#{rank+1} {outlet['name']}</b><br>"
                f"State: {outlet['state']}<br>"
                f"🛣️ Road: {outlet['road_km']} km{est_note}<br>"
                f"🛩️ Aerial: {outlet['aerial_km']} km<br>"
                f"{status_txt}",
                max_width=280
            ),
            tooltip=f"#{rank+1} {outlet['name']} — {outlet['road_km']} km",
            icon=folium.Icon(icon="industry", prefix="fa", color=top5_colors[rank])
        ).add_to(m)
        # Dashed line from pincode to outlet
        line_color = top5_colors[rank] if top5_colors[rank] not in ["lightgray", "cadetblue"] else "#888"
        folium.PolyLine(
            locations=[[pc_lat, pc_lon], [outlet["lat"], outlet["lon"]]],
            color=line_color,
            weight=2,
            opacity=0.65,
            dash_array="8 4",
            tooltip=f"#{rank+1} {outlet['name']}: {outlet['aerial_km']} km aerial"
        ).add_to(m)

# Render Batch Suggested Pincodes if checked
if plot_pincodes and st.session_state.suggested_outlets_df is not None:
    df_sug = st.session_state.suggested_outlets_df
    for _, row in df_sug.head(150).iterrows():
        p_lat = row.get('Pincode_Lat')
        p_lon = row.get('Pincode_Lon')
        if pd.notna(p_lat) and pd.notna(p_lon):
            status = row.get('Coverage_Status')
            color = "green" if status == "Covered" else "orange"
            folium.CircleMarker(
                location=[float(p_lat), float(p_lon)],
                radius=5,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
                popup=f"<b>PIN: {row.get('Pincode', '')}</b><br>Nearest Outlet: {row.get('Suggested_Outlet')}<br>Road Dist: {row.get('Road_Distance_km')} km<br>Status: {status}",
                tooltip=f"PIN: {row.get('Pincode')} -> {row.get('Suggested_Outlet')} ({row.get('Road_Distance_km')} km)"
            ).add_to(m)

# Fit Map Bounds
if valid_outlets or distance_result:
    try:
        pts = [[float(o['lat']), float(o['lon'])] for o in valid_outlets]
        if distance_result:
            pts.append(list(distance_result["pincode_coords"]))
        min_lat = min(p[0] for p in pts)
        max_lat = max(p[0] for p in pts)
        min_lon = min(p[1] for p in pts)
        max_lon = max(p[1] for p in pts)
        m.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]])
    except Exception:
        pass

# Display Folium Map
import hashlib
map_hash = hashlib.md5(json.dumps(st.session_state.outlets, sort_keys=True).encode()).hexdigest()
st_data = st_folium(m, width=1200, height=560, key=f"map_{map_hash}_{distance_result is not None}_{plot_pincodes}", returned_objects=[])

# 5. COLLAPSIBLE OUTLETS DIRECTORY & STATE BREAKDOWN
with st.expander("🏢 View All Outlets & State Distribution Directory", expanded=False):
    if valid_outlets:
        df_all_outlets = pd.DataFrame(valid_outlets)
        
        dir_c1, dir_c2 = st.columns([1, 2])
        with dir_c1:
            state_counts = df_all_outlets['state'].value_counts().reset_index()
            state_counts.columns = ['State', 'Outlet Count']
            st.dataframe(state_counts, use_container_width=True, height=240)
            
        with dir_c2:
            state_filter_dir = st.selectbox(
                "Filter Outlets by State",
                ["All States"] + sorted(list(df_all_outlets['state'].unique())),
                key="dir_state_filter"
            )
            df_filtered_dir = df_all_outlets if state_filter_dir == "All States" else df_all_outlets[df_all_outlets['state'] == state_filter_dir]
            st.dataframe(df_filtered_dir[['name', 'state', 'lat', 'lon', 'radius_km']], use_container_width=True, height=240)

# 6. AI ASSISTANT & DOCUMENT ANALYST SECTION
render_ai_chat_section(
    outlets=st.session_state.get('outlets', []),
    distance_result=st.session_state.get('distance_result'),
    batch_result_df=st.session_state.get('batch_result_df'),
    suggested_outlets_df=st.session_state.get('suggested_outlets_df'),
    valid_outlets=valid_outlets,
    find_nearest_outlet_func=find_nearest_outlet_for_pincode,
    pincode_cache=st.session_state.pincode_cache
)
