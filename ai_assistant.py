import os
import json
import io
import time
import pandas as pd
import pypdf
import streamlit as st
from geopy.geocoders import Nominatim

# Built-in Default Gemini API Key (User provided)
DEFAULT_GEMINI_KEY = "AQ.Ab8RN6IbLpCBdLpOOdd-3llJxzuvvIylUWmt5-4NBqlo40APYA"

def get_default_api_key(provider="Google Gemini"):
    """
    Retrieves the API key from st.secrets, environment, .env, or hardcoded default.
    """
    if "Gemini" in provider:
        try:
            if "GEMINI_API_KEY" in st.secrets:
                return str(st.secrets["GEMINI_API_KEY"]).strip()
        except Exception:
            pass
            
        env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if env_key and env_key.strip():
            return env_key.strip()
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("GEMINI_API_KEY="):
                            k = line.split("=", 1)[1].strip().strip('"').strip("'")
                            if k:
                                return k
            except Exception:
                pass
        return DEFAULT_GEMINI_KEY
    elif "OpenAI" in provider:
        try:
            if "OPENAI_API_KEY" in st.secrets:
                return str(st.secrets["OPENAI_API_KEY"]).strip()
        except Exception:
            pass
        return os.environ.get("OPENAI_API_KEY", "")
    return ""


def parse_uploaded_file(uploaded_file):
    """
    Extracts text, metadata, and structured data from an uploaded file.
    Supports CSV, Excel (.xlsx, .xls), PDF, JSON, TXT, MD, etc.
    """
    if uploaded_file is None:
        return None
        
    filename = uploaded_file.name
    file_bytes = uploaded_file.getvalue()
    file_ext = os.path.splitext(filename)[1].lower()
    
    result = {
        "filename": filename,
        "extension": file_ext,
        "type": "unknown",
        "summary": "",
        "content_text": "",
        "df_preview": None,
        "df_raw": None,
        "row_count": 0,
        "col_count": 0,
        "columns": []
    }
    
    try:
        if file_ext == ".csv":
            df = pd.read_csv(io.BytesIO(file_bytes))
            result["type"] = "table"
            result["df_raw"] = df
            result["row_count"] = len(df)
            result["col_count"] = len(df.columns)
            result["columns"] = list(df.columns)
            result["df_preview"] = df.head(10)
            
            stats_desc = df.describe(include='all').to_string() if not df.empty else "Empty dataset"
            sample_md = df.head(10).to_markdown()
            result["summary"] = f"CSV File '{filename}' with {len(df)} rows and {len(df.columns)} columns: {', '.join(df.columns)}"
            result["content_text"] = (
                f"### Uploaded CSV File: {filename}\n"
                f"- Total Rows: {len(df)}\n"
                f"- Total Columns: {len(df.columns)} ({', '.join(df.columns)})\n\n"
                f"#### First 10 Rows:\n{sample_md}\n\n"
                f"#### Summary Statistics:\n```\n{stats_desc}\n```"
            )
            
        elif file_ext in [".xlsx", ".xls"]:
            excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
            sheet_names = excel_file.sheet_names
            dfs = {}
            summary_parts = []
            preview_parts = []
            
            total_rows = 0
            for sheet in sheet_names:
                sheet_df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=sheet)
                dfs[sheet] = sheet_df
                total_rows += len(sheet_df)
                summary_parts.append(f"Sheet '{sheet}': {len(sheet_df)} rows, {len(sheet_df.columns)} cols ({', '.join([str(c) for c in sheet_df.columns])})")
                preview_parts.append(f"##### Sheet: {sheet}\n{sheet_df.head(5).to_markdown()}\n")
                
            first_df = dfs[sheet_names[0]] if sheet_names else pd.DataFrame()
            result["type"] = "excel"
            result["df_raw"] = first_df
            result["row_count"] = total_rows
            result["col_count"] = len(first_df.columns) if not first_df.empty else 0
            result["columns"] = list(first_df.columns) if not first_df.empty else []
            result["df_preview"] = first_df.head(10)
            result["summary"] = f"Excel File '{filename}' with sheets: {', '.join(sheet_names)}"
            result["content_text"] = (
                f"### Uploaded Excel File: {filename}\n"
                f"- Sheets: {', '.join(sheet_names)}\n"
                f"- Details:\n" + "\n".join([f"  - {s}" for s in summary_parts]) + "\n\n"
                f"#### Previews:\n" + "\n".join(preview_parts)
            )
            
        elif file_ext == ".pdf":
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            pages_text = []
            for i, page in enumerate(reader.pages):
                extracted = page.extract_text() or ""
                pages_text.append(f"--- Page {i+1} ---\n{extracted.strip()}")
                
            full_text = "\n\n".join(pages_text)
            truncated_text = full_text[:40000]
            if len(full_text) > 40000:
                truncated_text += "\n\n... [Content truncated due to length] ..."
                
            result["type"] = "pdf"
            result["row_count"] = len(reader.pages)
            result["summary"] = f"PDF Document '{filename}' with {len(reader.pages)} pages."
            result["content_text"] = f"### Uploaded PDF Document: {filename} ({len(reader.pages)} pages)\n\n{truncated_text}"
            
        elif file_ext in [".json"]:
            raw_str = file_bytes.decode("utf-8", errors="ignore")
            parsed_json = json.loads(raw_str)
            formatted_json = json.dumps(parsed_json, indent=2)[:30000]
            result["type"] = "json"
            result["summary"] = f"JSON File '{filename}'"
            result["content_text"] = f"### Uploaded JSON File: {filename}\n```json\n{formatted_json}\n```"
            
        else: # .txt, .md, .py, .log, etc.
            text_content = file_bytes.decode("utf-8", errors="ignore")
            preview_text = text_content[:30000]
            if len(text_content) > 30000:
                preview_text += "\n\n... [Content truncated due to length] ..."
            result["type"] = "text"
            result["summary"] = f"Text File '{filename}' ({len(text_content)} characters)"
            result["content_text"] = f"### Uploaded File: {filename}\n\n```\n{preview_text}\n```"
            
    except Exception as e:
        result["summary"] = f"File '{filename}' (parsing error: {str(e)})"
        result["content_text"] = f"### Uploaded File: {filename}\n(Error extracting content: {str(e)})"
        
    return result


def build_system_context(outlets, distance_result=None, batch_result_df=None, suggested_outlets_df=None, file_context=None):
    """
    Constructs the knowledge context provided to the AI model.
    """
    context_lines = [
        "You are an expert AI Assistant and Data/Logistics Analyst for the 'Gau Sampurna Outlet Coverage Dashboard'.",
        "Gau Sampurna is a premier dairy & organic network delivering fresh milk, ghee, and dairy products across India.",
        "Your capabilities include:",
        "1. Answering questions about the outlet network, coverage radius, coordinates, and state distribution.",
        "2. Answering questions about 'Suggested Outlets' (which outlet is closest to customer pincodes, coverage % rate).",
        "3. Answering questions, summarizing, or querying any file the user uploaded (CSV, Excel, PDF, JSON, TXT).",
        "4. Assisting with logistics, road distances, routing calculations, pincode coverage, and business insights.",
        "5. Answering general questions, performing calculations, and providing well-structured, insightful responses in clean Markdown.",
        "\n--- CURRENT DASHBOARD DATA ---"
    ]
    
    if outlets:
        df_outlets = pd.DataFrame(outlets)
        context_lines.append(f"Total Outlets Loaded: {len(outlets)}")
        
        if 'state' in df_outlets.columns:
            state_counts = df_outlets['state'].value_counts().to_dict()
            state_str = ", ".join([f"{k}: {v}" for k, v in state_counts.items()])
            context_lines.append(f"Outlets per State: {state_str}")
            
        outlets_json_summary = json.dumps(outlets[:60], indent=2)
        context_lines.append(f"List of Outlets (sample/first 60):\n{outlets_json_summary}")
    else:
        context_lines.append("No outlet data currently loaded from Google Sheets.")
        
    if distance_result:
        context_lines.append(
            f"\n--- RECENT SINGLE DISTANCE CALCULATION ---\n"
            f"- Target Outlet: {distance_result.get('outlet_name')}\n"
            f"- Pincode Queried: {distance_result.get('pincode')}\n"
            f"- Road Distance: {distance_result.get('distance'):.2f} km\n"
            f"- Outlet Coordinates: {distance_result.get('outlet_coords')}\n"
            f"- Pincode Coordinates: {distance_result.get('pincode_coords')}"
        )
        
    if batch_result_df is not None and not batch_result_df.empty:
        context_lines.append(
            f"\n--- RECENT BATCH DISTANCE RESULTS (FIXED OUTLET) ---\n"
            f"- Total Pincodes Processed: {len(batch_result_df)}\n"
            f"- Sample Results:\n{batch_result_df.head(5).to_markdown()}"
        )

    if suggested_outlets_df is not None and not suggested_outlets_df.empty:
        total_p = len(suggested_outlets_df)
        covered_p = len(suggested_outlets_df[suggested_outlets_df["Coverage_Status"] == "Covered"]) if "Coverage_Status" in suggested_outlets_df.columns else 0
        top_assigned = suggested_outlets_df["Suggested_Outlet"].value_counts().head(6).to_dict() if "Suggested_Outlet" in suggested_outlets_df.columns else {}
        top_assigned_str = ", ".join([f"{k}: {v}" for k, v in top_assigned.items()])
        context_lines.append(
            f"\n--- RECENT SUGGESTED OUTLET BATCH RESULTS (NEAREST OUTLETS) ---\n"
            f"- Total Pincodes Analyzed: {total_p}\n"
            f"- Pincodes Within Coverage Radius: {covered_p} ({covered_p/total_p*100:.1f}%)\n"
            f"- Pincodes Outside Coverage Radius: {total_p - covered_p}\n"
            f"- Top Assigned Outlets: {top_assigned_str}\n"
            f"- Preview of Results:\n{suggested_outlets_df.head(10).to_markdown()}"
        )
        
    if file_context and file_context.get("content_text"):
        context_lines.append(f"\n--- UPLOADED USER FILE CONTEXT ---\n{file_context['content_text']}")
        
    context_lines.append(
        "\nAlways provide direct, polite, helpful, and insightful answers. Use bolding, tables, bullet points, and code formatting where helpful."
    )
    
    return "\n".join(context_lines)


def smart_offline_analyst(prompt, outlets, suggested_outlets_df=None, file_context=None):
    """
    Built-in offline intelligent analyzer that answers queries without requiring an API key.
    """
    p_lower = prompt.lower().strip()
    
    if suggested_outlets_df is not None and not suggested_outlets_df.empty and any(kw in p_lower for kw in ["suggest", "nearest", "batch", "assign", "rate", "coverage"]):
        total_p = len(suggested_outlets_df)
        covered_p = len(suggested_outlets_df[suggested_outlets_df["Coverage_Status"] == "Covered"]) if "Coverage_Status" in suggested_outlets_df.columns else 0
        top_assigned = suggested_outlets_df["Suggested_Outlet"].value_counts().to_dict() if "Suggested_Outlet" in suggested_outlets_df.columns else {}
        top_md = "\n".join([f"- **{k}**: {v} pincodes" for k, v in list(top_assigned.items())[:5]])
        return (
            f"### 🎯 Suggested Outlets Analysis Summary:\n\n"
            f"- **Total Pincodes Analyzed**: {total_p}\n"
            f"- **Covered Within Radius**: {covered_p} ({covered_p/total_p*100:.1f}%)\n"
            f"- **Outside Radius**: {total_p - covered_p}\n\n"
            f"**Top Assigned Outlets:**\n{top_md}"
        )

    if file_context and any(kw in p_lower for kw in ["file", "csv", "excel", "pdf", "rows", "columns", "data", "upload", "summary", "stats"]):
        fn = file_context.get("filename", "Uploaded File")
        ftype = file_context.get("type", "unknown")
        
        if any(kw in p_lower for kw in ["summary", "summarize", "about", "overview", "what is"]):
            return (
                f"### 📄 Analysis of Uploaded File: `{fn}`\n\n"
                f"- **File Type**: {ftype.upper()}\n"
                f"- **Details**: {file_context.get('summary', '')}\n\n"
                f"**Preview of Extracted Data:**\n\n"
                f"{file_context.get('content_text', '')[:2500]}"
            )
        elif any(kw in p_lower for kw in ["column", "columns", "fields", "headers"]):
            cols = file_context.get("columns", [])
            if cols:
                cols_formatted = "\n".join([f"- `{c}`" for c in cols])
                return f"### 📋 Columns in `{fn}` ({len(cols)} total):\n\n{cols_formatted}"
            else:
                return f"No tabular columns detected in `{fn}`."
        elif any(kw in p_lower for kw in ["row", "rows", "count", "size"]):
            return f"### 📊 `{fn}` contains **{file_context.get('row_count', 0)}** rows/pages."
            
    if outlets:
        df_outlets = pd.DataFrame(outlets)
        
        if any(kw in p_lower for kw in ["how many outlet", "total outlet", "number of outlet", "count"]):
            return f"🗺️ There are currently **{len(outlets)}** outlets loaded in the system across **{df_outlets['state'].nunique() if 'state' in df_outlets else 'various'}** states."
            
        if any(kw in p_lower for kw in ["state", "states", "distribution", "breakdown"]):
            if 'state' in df_outlets.columns:
                counts = df_outlets['state'].value_counts()
                table_md = "| State | Outlet Count |\n|---|---|\n" + "\n".join([f"| {state} | {count} |" for state, count in counts.items()])
                return f"### 📍 Outlet Distribution by State:\n\n{table_md}\n\n**Total Outlets:** {len(outlets)}"
                
        if any(kw in p_lower for kw in ["list", "all outlet", "names"]):
            summary_list = "\n".join([f"- **{o['name']}** ({o.get('state', 'Unknown')}) - Lat: `{o['lat']}`, Lon: `{o['lon']}`, Radius: `{o.get('radius_km', 60)} km`" for o in outlets[:30]])
            more_note = f"\n\n*(Showing first 30 of {len(outlets)} outlets)*" if len(outlets) > 30 else ""
            return f"### 🏢 Outlets List:\n\n{summary_list}{more_note}"
            
        for o in outlets:
            if o['name'].lower() in p_lower:
                return (
                    f"### 📍 Outlet Details: **{o['name']}**\n\n"
                    f"- **State:** {o.get('state', 'N/A')}\n"
                    f"- **Coordinates:** Latitude `{o.get('lat')}`, Longitude `{o.get('lon')}`\n"
                    f"- **Coverage Radius:** `{o.get('radius_km', 60)} km`\n"
                    f"- **Google Maps Link:** [View on Maps](https://www.google.com/maps?q={o.get('lat')},{o.get('lon')})"
                )
                
    file_info = f"\n- Uploaded file: `{file_context['filename']}`" if file_context else ""
    return (
        f"### 🤖 Gau Sampurna Smart Assistant\n\n"
        f"I received your question: *\"{prompt}\"*\n\n"
        f"**Available Context:**\n"
        f"- Total Outlets in System: **{len(outlets) if outlets else 0}**{file_info}\n\n"
        f"💡 Tip: Gemini AI is active and will answer questions automatically when an API key is connected."
    )


def query_llm(provider, api_key, model_name, messages, user_prompt, system_context):
    """
    Executes query against selected AI provider (Google Gemini or OpenAI) with automatic fallback cascade.
    """
    if "Gemini" in provider:
        from google import genai
        from google.genai import types
        
        client = genai.Client(api_key=api_key)
        clean_model = model_name.split()[0].strip() if model_name else "gemini-3.5-flash"
        fallback_models = [clean_model, "gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
        candidate_models = list(dict.fromkeys(fallback_models))
        
        chat_contents = []
        for m in messages[-8:]:
            role = "user" if m.get("role") == "user" else "model"
            chat_contents.append(
                types.Content(role=role, parts=[types.Part.from_text(text=m.get("content", ""))])
            )
        chat_contents.append(
            types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])
        )
        
        last_error = None
        for m_name in candidate_models:
            try:
                response = client.models.generate_content(
                    model=m_name,
                    contents=chat_contents,
                    config=types.GenerateContentConfig(
                        system_instruction=system_context,
                        temperature=0.7
                    )
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                last_error = e
                continue
                
        raise Exception(f"Gemini API error across models: {last_error}")
                
    elif "OpenAI" in provider:
        try:
            import openai
            client = openai.OpenAI(api_key=api_key)
            
            openai_messages = [{"role": "system", "content": system_context}]
            for m in messages[-8:]:
                openai_messages.append({"role": m["role"], "content": m["content"]})
            openai_messages.append({"role": "user", "content": user_prompt})
            
            clean_openai_model = model_name.split()[0].strip() if model_name and "gpt" in model_name else "gpt-4o-mini"
            
            response = client.chat.completions.create(
                model=clean_openai_model,
                messages=openai_messages,
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            raise Exception(f"OpenAI API error: {str(e)}")
            
    else:
        raise ValueError(f"Unsupported provider: {provider}")


def render_ai_chat_section(outlets, distance_result=None, batch_result_df=None, suggested_outlets_df=None, valid_outlets=None, find_nearest_outlet_func=None, pincode_cache=None):
    """
    Renders the interactive AI Chat and File Analysis section at the bottom of the Streamlit dashboard.
    """
    st.markdown("---")
    
    # Initialize session state for AI chat
    if "ai_chat_messages" not in st.session_state:
        st.session_state.ai_chat_messages = [
            {
                "role": "assistant",
                "content": "👋 Hello! I am your **Gau Sampurna AI Assistant & Logistics Analyst** powered by Google Gemini.\n\nYou can:\n- 🎯 Inquire about **Suggested Outlets** and nearest outlet coverage for your pincode batches.\n- 🗺️ Ask questions about **outlet locations, coverage radius, and state breakdowns**.\n- 📁 **Upload any file** (CSV, Excel, PDF, TXT, JSON) to analyze, summarize, or query data.\n- 🛣️ Inquire about **road distances, delivery routes, and logistics optimization**."
            }
        ]
        
    if "uploaded_file_context" not in st.session_state:
        st.session_state.uploaded_file_context = None

    if "pending_quick_prompt" not in st.session_state:
        st.session_state.pending_quick_prompt = None

    # Section Header with styling
    col_header1, col_header2 = st.columns([3, 1])
    with col_header1:
        st.subheader("💬 AI Assistant & Logistics Analyst")
        st.caption("Ask questions about your outlets, suggested nearest outlets, road routes, or upload any file (CSV, Excel, PDF, TXT, JSON) to analyze it.")
    with col_header2:
        if st.button("🧹 Clear Chat History", key="clear_chat_btn", use_container_width=True):
            st.session_state.ai_chat_messages = [
                {
                    "role": "assistant",
                    "content": "Chat history cleared. How can I help you today?"
                }
            ]
            st.rerun()

    # AI Configuration and File Upload Controls
    with st.expander("⚙️ AI Configuration & File Attachment", expanded=False):
        cfg_col1, cfg_col2 = st.columns([1, 1])
        
        with cfg_col1:
            st.markdown("##### 📁 Share / Upload a File")
            uploaded_file = st.file_uploader(
                "Upload a file to ask questions about (CSV, Excel, PDF, TXT, JSON)",
                type=["csv", "xlsx", "xls", "pdf", "txt", "json", "md", "log"],
                key="ai_file_uploader_input"
            )
            
            if uploaded_file is not None:
                curr_filename = st.session_state.uploaded_file_context.get("filename") if st.session_state.uploaded_file_context else None
                if curr_filename != uploaded_file.name:
                    with st.spinner(f"Analyzing `{uploaded_file.name}`..."):
                        file_ctx = parse_uploaded_file(uploaded_file)
                        st.session_state.uploaded_file_context = file_ctx
                        st.session_state.ai_chat_messages.append({
                            "role": "assistant",
                            "content": f"📁 **File Attached:** `{uploaded_file.name}` ({file_ctx.get('summary', '')})\n\nYou can now ask me anything about this file!"
                        })
                        st.rerun()
            else:
                if st.session_state.uploaded_file_context is not None:
                    st.session_state.uploaded_file_context = None
                
            if st.session_state.uploaded_file_context:
                ctx = st.session_state.uploaded_file_context
                st.success(f"✅ Active File: **{ctx['filename']}** ({ctx['summary']})")
                
                # Check if uploaded file contains pincodes for instant processing
                df_raw = ctx.get("df_raw")
                if df_raw is not None and not df_raw.empty:
                    pin_col_candidate = None
                    for c in df_raw.columns:
                        if any(k in str(c).lower() for k in ['pincode', 'pin code', 'pin', 'postal', 'zip', 'zipcode']):
                            pin_col_candidate = c
                            break
                            
                    if pin_col_candidate and find_nearest_outlet_func and valid_outlets:
                        st.info(f"💡 Pincode column **`{pin_col_candidate}`** detected in this file.")
                        if st.button("🎯 Map Pincodes to Nearest Outlets & Download Excel", key="btn_chat_match_pincodes", use_container_width=True, type="primary"):
                            with st.spinner(f"Matching {len(df_raw)} pincodes to nearest outlets..."):
                                geolocator = Nominatim(user_agent="gau_sampurna_chat_matcher", timeout=10)
                                cache = pincode_cache if pincode_cache is not None else {}
                                results = []
                                for _, r in df_raw.iterrows():
                                    pval = str(r[pin_col_candidate]).strip()
                                    if pval.endswith(".0"): pval = pval[:-2]
                                    res = find_nearest_outlet_func(pval, valid_outlets, geolocator, cache)
                                    results.append(res)
                                    if pval not in cache:
                                        time.sleep(0.4)
                                        
                                df_out = df_raw.copy()
                                df_out['Suggested_Outlet'] = [r.get('suggested_outlet', 'Not Found') if r.get('found') else 'Not Found' for r in results]
                                df_out['Outlet_State'] = [r.get('outlet_state', '-') if r.get('found') else '-' for r in results]
                                df_out['Road_Distance_km'] = [r.get('road_distance_km', None) if r.get('found') else None for r in results]
                                df_out['Coverage_Status'] = [r.get('coverage_status', r.get('error', 'Error')) for r in results]
                                df_out['Outlet_Radius_km'] = [r.get('outlet_radius_km', None) if r.get('found') else None for r in results]
                                df_out['Aerial_Distance_km'] = [r.get('aerial_distance_km', None) if r.get('found') else None for r in results]
                                df_out['Pincode_Lat'] = [r.get('pincode_coords', (None, None))[0] if r.get('found') else None for r in results]
                                df_out['Pincode_Lon'] = [r.get('pincode_coords', (None, None))[1] if r.get('found') else None for r in results]
                                
                                # Generate Excel buffer
                                ex_buf = io.BytesIO()
                                with pd.ExcelWriter(ex_buf, engine='openpyxl') as writer:
                                    df_out.to_excel(writer, index=False, sheet_name='Suggested_Outlets')
                                excel_bytes = ex_buf.getvalue()
                                
                                total_p = len(df_out)
                                cov_p = len(df_out[df_out['Coverage_Status'] == 'Covered'])
                                
                                st.session_state.suggested_outlets_df = df_out
                                st.session_state.ai_chat_messages.append({
                                    "role": "assistant",
                                    "content": f"🎯 **Pincodes Mapped to Nearest Outlets!**\n\n- Total Pincodes: **{total_p}**\n- Within Radius: **{cov_p}** ({cov_p/total_p*100:.1f}%)\n- Outside Radius: **{total_p - cov_p}**\n\nClick the button below to download your enriched Excel file with nearest outlets mapped:",
                                    "excel_bytes": excel_bytes,
                                    "excel_filename": f"suggested_outlets_{ctx['filename']}.xlsx"
                                })
                                st.rerun()
                                
                if ctx.get("df_preview") is not None:
                    with st.expander("👀 Preview Table Data", expanded=False):
                        st.dataframe(ctx["df_preview"], use_container_width=True)

        with cfg_col2:
            st.markdown("##### 🧠 AI Provider & Key Settings")
            provider = st.selectbox(
                "Select AI Provider",
                ["Google Gemini (Integrated & Recommended)", "OpenAI", "Built-in Offline Analyst"],
                index=0,
                key="ai_provider_select"
            )
            
            default_key = get_default_api_key(provider)
            
            api_key_input = st.text_input(
                f"Enter {'Gemini' if 'Gemini' in provider else 'OpenAI'} API Key",
                value=default_key,
                type="password",
                placeholder="AQ... / AIza... or sk-...",
                help="Gemini API Key is pre-configured and active.",
                key="user_entered_api_key"
            )
            
            if "Gemini" in provider:
                model_name = st.selectbox(
                    "Model",
                    ["gemini-3.5-flash (Recommended)", "gemini-3.7-flash (Advanced Reasoning)", "gemini-3.5-flash-lite (Ultra Fast)", "gemini-3.1-flash-lite"],
                    index=0,
                    key="gemini_model_choice"
                )
                if api_key_input:
                    st.caption("🟢 **Gemini API Key Connected** • Ready for instant queries")
            elif "OpenAI" in provider:
                model_name = st.selectbox(
                    "Model",
                    ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"],
                    index=0,
                    key="openai_model_choice"
                )
            else:
                model_name = "built-in"
                st.info("ℹ️ Using built-in offline analyzer.")

    # Status Banner for Gemini
    if "Gemini" in provider and api_key_input:
        st.markdown(
            """
            <div style="background-color: rgba(76, 175, 80, 0.1); border-left: 4px solid #4CAF50; padding: 8px 12px; border-radius: 4px; margin-bottom: 12px;">
                <span style="font-size: 13px; color: #2e7d32; font-weight: 600;">🟢 Google Gemini Active</span>
                <span style="font-size: 13px; color: #555; margin-left: 10px;">Ask anything about outlets, coverage, suggested nearest outlets, pincode distances, or attached files.</span>
            </div>
            """,
            unsafe_allow_html=True
        )

    # Quick Action Prompt Chips
    st.markdown("##### ⚡ Quick Questions:")
    quick_cols = st.columns(4)
    
    if quick_cols[0].button("📊 Outlets Summary by State", use_container_width=True):
        st.session_state.pending_quick_prompt = "Give me a breakdown of all outlets grouped by state and list key statistics."
        st.rerun()
    if quick_cols[1].button("🎯 Analyze Suggested Outlets", use_container_width=True):
        st.session_state.pending_quick_prompt = "Summarize the suggested nearest outlet batch results: which outlets have the highest pincode assignments and what is our coverage percentage?"
        st.rerun()
    if quick_cols[2].button("🔍 Check Outlet Coverage", use_container_width=True):
        st.session_state.pending_quick_prompt = "What is the average coverage radius and which outlets have the widest coverage?"
        st.rerun()
    if quick_cols[3].button("📄 Analyze Uploaded File", use_container_width=True):
        st.session_state.pending_quick_prompt = "Summarize the key information and insights from the uploaded file."
        st.rerun()

    # Determine prompt to process
    user_input = st.chat_input("Ask a question about your outlets, logistics, or uploaded file...")
    prompt_to_process = None
    
    if user_input:
        prompt_to_process = user_input
    elif st.session_state.pending_quick_prompt:
        prompt_to_process = st.session_state.pending_quick_prompt
        st.session_state.pending_quick_prompt = None

    # Render Chat History
    for idx, msg in enumerate(st.session_state.ai_chat_messages):
        with st.chat_message(msg["role"], avatar="🤖" if msg["role"] == "assistant" else "👤"):
            st.markdown(msg["content"])
            if "excel_bytes" in msg and msg["excel_bytes"]:
                st.download_button(
                    label="📥 Download Processed Excel (.xlsx)",
                    data=msg["excel_bytes"],
                    file_name=msg.get("excel_filename", "suggested_outlets.xlsx"),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"chat_dl_excel_{idx}",
                    type="primary"
                )

    # Process new prompt
    if prompt_to_process:
        st.session_state.ai_chat_messages.append({"role": "user", "content": prompt_to_process})
        with st.chat_message("user", avatar="👤"):
            st.markdown(prompt_to_process)
            
        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("🤖 Gemini is thinking..."):
                file_ctx = st.session_state.uploaded_file_context
                system_ctx = build_system_context(
                    outlets=outlets,
                    distance_result=distance_result,
                    batch_result_df=batch_result_df,
                    suggested_outlets_df=suggested_outlets_df,
                    file_context=file_ctx
                )
                
                selected_provider = "Google Gemini" if "Gemini" in provider else ("OpenAI" if "OpenAI" in provider else "Offline")
                api_key = api_key_input.strip() if api_key_input else get_default_api_key(provider)
                
                response_text = ""
                if selected_provider != "Offline" and api_key:
                    try:
                        response_text = query_llm(
                            provider=selected_provider,
                            api_key=api_key,
                            model_name=model_name,
                            messages=st.session_state.ai_chat_messages[:-1],
                            user_prompt=prompt_to_process,
                            system_context=system_ctx
                        )
                    except Exception as e:
                        st.warning(f"Note: API error ({e}). Falling back to built-in analyst.")
                        response_text = smart_offline_analyst(prompt_to_process, outlets, suggested_outlets_df, file_ctx)
                else:
                    response_text = smart_offline_analyst(prompt_to_process, outlets, suggested_outlets_df, file_ctx)
                    
                st.markdown(response_text)
                st.session_state.ai_chat_messages.append({"role": "assistant", "content": response_text})
