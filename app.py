import streamlit as st
import pandas as pd
from datetime import datetime
import re
import io

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="AV7 Gap Analyzer V4", layout="wide")

# --- HELPER FUNCTIONS ---
def clean_flight_number(flight_str):
    if pd.isna(flight_str): return ""
    return re.sub(r'[^A-Za-z0-9]', '', str(flight_str)).upper()

def smart_parse(paste_string, expected_cols):
    """
     Intelligently parses pasted data.
     1. Tries to read it normally (assuming headers exist).
     2. If the headers don't match, checks if the COLUMN COUNT matches.
     3. If column count matches, it assumes data was pasted without headers.
    """
    try:
        if not paste_string: return None
        # Attempt 1: Read assuming headers are present
        df = pd.read_csv(io.StringIO(paste_string), sep='\t')
        df.columns = [str(c).strip() for c in df.columns] 
        
        # Check if perfect match
        if all(col in df.columns for col in expected_cols):
            return df
        
        # Attempt 2: Check if column counts match (User forgot headers)
        if len(df.columns) == len(expected_cols):
            df = pd.read_csv(io.StringIO(paste_string), sep='\t', header=None)
            df.columns = expected_cols
            st.toast(f"⚠️ Fixed missing headers for {expected_cols[0]} automatically!", icon="🔧")
            return df
            
        return None 
    except Exception:
        return None

# --- SIDEBAR: CONFIGURATION ---
st.sidebar.header("Configuration")

# 1. Gap Threshold (UPDATED DEFAULT TO 5)
st.sidebar.subheader("Sensitivity")
slack_minutes = st.sidebar.slider("Slack Minutes", 15, 120, 60)
series_jump_threshold = st.sidebar.number_input(
    "Ignore gaps larger than (Receipts)", 
    value=5, 
    min_value=1,
    help="If a gap is larger than this number (e.g. 10 missing receipts in a row), the tool ignores it. Set to '2' to find only single missing receipts."
)

# 2. Exclusions
st.sidebar.subheader("Exclusions")
ignore_av7_input = st.sidebar.text_area("Ignore specific AV7s", placeholder="890100, 890101")
ignore_flight_input = st.sidebar.text_area("Ignore Flight Numbers", placeholder="6E123")
ignore_prefixes_input = st.sidebar.text_input("Ignore Prefixes", placeholder="Optional (e.g., 99)")

# Process Exclusions
known_cancelled_av7 = set()
if ignore_av7_input:
    for item in ignore_av7_input.split(','):
        clean_item = re.sub(r'[^0-9]', '', item)
        if clean_item: known_cancelled_av7.add(int(clean_item))

ignored_flights = set()
if ignore_flight_input:
    for item in ignore_flight_input.split(','):
        cleaned = clean_flight_number(item)
        if cleaned: ignored_flights.add(cleaned)

ignore_prefixes = tuple()
if ignore_prefixes_input:
    ignore_prefixes = tuple(p.strip() for p in ignore_prefixes_input.split(',') if p.strip())

# --- MAIN INTERFACE ---
st.title("✈️ AV7 Gap Analyzer")
st.markdown("Copy your data directly from Excel and paste it below.")

col1, col2 = st.columns(2)

# Define expected columns for validation
REQ_REFUEL = ['AV7', 'Flight', 'Refuel_Time']
REQ_SCHED = ['Flight', 'STD']

with col1:
    st.subheader("1. Refueling Record")
    st.info(f"Required: 3 Columns ({', '.join(REQ_REFUEL)})")
    refuel_text = st.text_area("Paste Refueling Data Here", height=300)

with col2:
    st.subheader("2. Flight Schedule")
    st.info(f"Required: 2 Columns ({', '.join(REQ_SCHED)})")
    schedule_text = st.text_area("Paste Schedule Data Here", height=300)

# --- LOGIC ---
if st.button("Analyze Gaps", type="primary"):
    if not refuel_text or not schedule_text:
        st.error("Please paste data into both boxes first.")
    else:
        df_refuel = smart_parse(refuel_text, REQ_REFUEL)
        df_schedule = smart_parse(schedule_text, REQ_SCHED)

        if df_refuel is None:
            st.error(f"❌ Refuel Data Error: Columns don't match.\nExpected 3 columns: {REQ_REFUEL}")
        elif df_schedule is None:
            st.error(f"❌ Schedule Data Error: Columns don't match.\nExpected 2 columns: {REQ_SCHED}")
        else:
            # --- PROCESSING ---
            if ignore_prefixes:
                df_refuel = df_refuel[~df_refuel['AV7'].astype(str).str.startswith(ignore_prefixes, na=False)]
            
            df_refuel['Flight_Clean'] = df_refuel['Flight'].apply(clean_flight_number)
            df_schedule['Flight_Clean'] = df_schedule['Flight'].apply(clean_flight_number)

            df_refuel['AV7_Num'] = pd.to_numeric(df_refuel['AV7'], errors='coerce')
            df_refuel_clean = df_refuel.dropna(subset=['AV7_Num']).sort_values(by='AV7_Num').reset_index(drop=True)

            # Fix Time formats
            df_refuel_clean['Refuel_Time'] = pd.to_datetime(df_refuel_clean['Refuel_Time'].astype(str), format='%H:%M:%S', errors='coerce')

            def parse_std(val):
                if pd.isna(val): return pd.NaT
                s = str(val).split('.')[0].strip().zfill(4)
                try:
                    return datetime.strptime(s, '%H%M')
                except ValueError:
                    try: return datetime.strptime(s, '%H:%M')
                    except: return pd.NaT
            
            df_schedule['STD_Parsed'] = df_schedule['STD'].apply(parse_std)

            # Debug Expander
            with st.expander("🛠️ Debug: View Processed Data"):
                st.dataframe(df_refuel_clean.head())

            recorded_flights_clean = df_refuel_clean['Flight_Clean'].unique()
            missing_flights_df = df_schedule[~df_schedule['Flight_Clean'].isin(recorded_flights_clean)].copy()
            
            if ignored_flights:
                missing_flights_df = missing_flights_df[~missing_flights_df['Flight_Clean'].isin(ignored_flights)]

            def get_minutes(dt):
                if pd.isnull(dt): return None
                return dt.hour * 60 + dt.minute
            missing_flights_df['STD_Minutes'] = missing_flights_df['STD_Parsed'].apply(get_minutes)

            predictions = []

            for i in range(len(df_refuel_clean) - 1):
                current_av7 = df_refuel_clean.loc[i, 'AV7_Num']
                next_av7 = df_refuel_clean.loc[i+1, 'AV7_Num']
                gap_size = next_av7 - current_av7

                # --- GAP LOGIC ---
                if gap_size > 1:
                    # Check if gap is too big (Ignore sequential blocks)
                    if gap_size > series_jump_threshold: 
                        continue 
                    
                    t_prev = df_refuel_clean.loc[i, 'Refuel_Time']
                    t_next = df_refuel_clean.loc[i+1, 'Refuel_Time']
                    
                    if pd.isnull(t_prev) or pd.isnull(t_next): continue

                    if t_next < t_prev:
                        start_time, end_time = t_next, t_prev
                        logic_note = "Swapped (Reverse)"
                    else:
                        start_time, end_time = t_prev, t_next
                        logic_note = "Normal"

                    start_mins = (start_time.hour * 60 + start_time.minute) - slack_minutes
                    end_mins = (end_time.hour * 60 + end_time.minute) + slack_minutes

                    candidates = []
                    for _, row in missing_flights_df.iterrows():
                        f_mins = row['STD_Minutes']
                        if pd.isnull(f_mins): continue
                        # Handle basic day boundaries if needed, keeping simple for now
                        if start_mins <= f_mins <= end_mins:
                            candidates.append(f"{row['Flight']} ({row['STD_Parsed'].strftime('%H:%M')})")

                    candidate_str = ", ".join(candidates) if candidates else "No flights found in window"

                    missing_range = range(int(current_av7) + 1, int(next_av7))
                    for missing_num in missing_range:
                        if missing_num in known_cancelled_av7: continue
                        predictions.append({
                            'Missing_AV7': missing_num,
                            'Window_Logic': logic_note,
                            'Window_Start': start_time.strftime('%H:%M'),
                            'Window_End': end_time.strftime('%H:%M'),
                            'POTENTIAL_FLIGHTS': candidate_str
                        })

            # --- RESULT DISPLAY ---
            if predictions:
                res_df = pd.DataFrame(predictions)
                st.success(f"Found {len(res_df)} missing receipts.")
                st.dataframe(res_df, use_container_width=True)
                
                buffer = io.BytesIO()
                # --- CRASH FIX: Changed engine to openpyxl ---
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    res_df.to_excel(writer, index=False, sheet_name='Sheet1')
                    
                st.download_button("📥 Download Results", buffer, "missing_av7_report.xlsx")
            else:
                st.warning("No missing AV7s found.")
                st.info("If you expected to see missing numbers but don't, check the 'Sensitivity' settings in the sidebar.")
