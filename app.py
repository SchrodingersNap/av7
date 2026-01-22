import streamlit as st
import pandas as pd
from datetime import datetime
import re
import io

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="AV7 Gap Analyzer V2", layout="wide")

# --- HELPER FUNCTIONS ---
def clean_flight_number(flight_str):
    if pd.isna(flight_str): return ""
    return re.sub(r'[^A-Za-z0-9]', '', str(flight_str)).upper()

def parse_pasted_data(paste_string):
    try:
        # Use StringIO to simulate a file object from the string
        return pd.read_csv(io.StringIO(paste_string), sep='\t')
    except Exception as e:
        return None

# --- SIDEBAR: CONFIGURATION ---
st.sidebar.header("Configuration")

# 1. Gap Threshold
st.sidebar.subheader("Sensitivity")
slack_minutes = st.sidebar.slider("Slack Minutes (Time Buffer)", 15, 120, 60, help="How many minutes before/after the gap to search for flights.")
series_jump_threshold = st.sidebar.number_input(
    "Max AV7 Jump Threshold", 
    value=1000, 
    help="If the gap between two receipts is larger than this (e.g., jumping from 100 to 5000), the tool assumes it's a different book series and won't report missing numbers."
)

# 2. Exclusions
st.sidebar.subheader("Exclusions")
ignore_av7_input = st.sidebar.text_area("Ignore specific AV7s", placeholder="890100, 890101")
ignore_flight_input = st.sidebar.text_area("Ignore Flight Numbers", placeholder="6E123, 6E-456")
ignore_prefixes_input = st.sidebar.text_input("Ignore Numbers Starting With", placeholder="Optional (e.g., 99)", help="Enter prefixes to exclude (comma separated). Leave empty to analyze ALL numbers.")

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
st.title("✈️ AV7 Gap Analyzer (Enhanced)")
st.markdown("Copy your data directly from Excel and paste it below.")

col1, col2 = st.columns(2)

with col1:
    st.subheader("1. Refueling Record")
    st.info("Required Columns: `AV7`, `Flight`, `Refuel_Time`")
    refuel_text = st.text_area("Paste Refueling Data Here", height=300)

with col2:
    st.subheader("2. Flight Schedule")
    st.info("Required Columns: `Flight`, `STD`")
    schedule_text = st.text_area("Paste Schedule Data Here", height=300)

# --- LOGIC ---
if st.button("Analyze Gaps", type="primary"):
    if not refuel_text or not schedule_text:
        st.error("Please paste data into both boxes first.")
    else:
        # 1. Parse Data
        df_refuel = parse_pasted_data(refuel_text)
        df_schedule = parse_pasted_data(schedule_text)

        if df_refuel is None or df_schedule is None:
            st.error("Could not parse data. Ensure you copied straight from Excel (Tab separated).")
        else:
            # Check Columns
            req_refuel = ['AV7', 'Flight', 'Refuel_Time']
            req_sched = ['Flight', 'STD']
            
            # Clean column names
            df_refuel.columns = [c.strip() for c in df_refuel.columns]
            df_schedule.columns = [c.strip() for c in df_schedule.columns]

            if not all(col in df_refuel.columns for col in req_refuel):
                st.error(f"Refuel data missing columns. Found: {list(df_refuel.columns)}. Expected: {req_refuel}")
            elif not all(col in df_schedule.columns for col in req_sched):
                st.error(f"Schedule data missing columns. Found: {list(df_schedule.columns)}. Expected: {req_sched}")
            else:
                # --- PROCESSING ---
                
                # 1. Filter Prefixes (User controlled now)
                if ignore_prefixes:
                    df_refuel = df_refuel[~df_refuel['AV7'].astype(str).str.startswith(ignore_prefixes, na=False)]
                
                # 2. Clean Data
                df_refuel['Flight_Clean'] = df_refuel['Flight'].apply(clean_flight_number)
                df_schedule['Flight_Clean'] = df_schedule['Flight'].apply(clean_flight_number)

                df_refuel['AV7_Num'] = pd.to_numeric(df_refuel['AV7'], errors='coerce')
                
                # Drop Invalid AV7s
                df_refuel_clean = df_refuel.dropna(subset=['AV7_Num']).sort_values(by='AV7_Num').reset_index(drop=True)

                # Fix Time formats
                df_refuel_clean['Refuel_Time'] = pd.to_datetime(df_refuel_clean['Refuel_Time'].astype(str), format='%H:%M:%S', errors='coerce')

                def parse_std(val):
                    if pd.isna(val): return pd.NaT
                    s = str(val).split('.')[0].strip().zfill(4)
                    try:
                        return datetime.strptime(s, '%H%M')
                    except ValueError:
                        try:
                            return datetime.strptime(s, '%H:%M')
                        except:
                            return pd.NaT
                
                df_schedule['STD_Parsed'] = df_schedule['STD'].apply(parse_std)

                # --- DEBUG SECTION ---
                with st.expander("🛠️ Debug: See Processed Data (Click to Expand)"):
                    st.write(f"Total Refueling Rows Read: {len(df_refuel)}")
                    st.write(f"Rows after Cleaning (Valid AV7 Numbers): {len(df_refuel_clean)}")
                    if len(df_refuel) != len(df_refuel_clean):
                        st.warning("⚠️ Some rows were dropped because 'AV7' was not a number.")
                    st.dataframe(df_refuel_clean.head())

                # Pool of candidates
                recorded_flights_clean = df_refuel_clean['Flight_Clean'].unique()
                missing_flights_df = df_schedule[~df_schedule['Flight_Clean'].isin(recorded_flights_clean)].copy()
                
                if ignored_flights:
                    missing_flights_df = missing_flights_df[~missing_flights_df['Flight_Clean'].isin(ignored_flights)]

                def get_minutes(dt):
                    if pd.isnull(dt): return None
                    return dt.hour * 60 + dt.minute
                missing_flights_df['STD_Minutes'] = missing_flights_df['STD_Parsed'].apply(get_minutes)

                predictions = []

                # Gap Search
                for i in range(len(df_refuel_clean) - 1):
                    current_av7 = df_refuel_clean.loc[i, 'AV7_Num']
                    next_av7 = df_refuel_clean.loc[i+1, 'AV7_Num']
                    gap_size = next_av7 - current_av7

                    # Check Threshold (Series Jump)
                    if gap_size > 1:
                        if gap_size > series_jump_threshold:
                            # Too big gap - assume different book/series
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
                            
                            # Handle midnight crossing (simple version)
                            # If start_mins is negative (e.g. 23:30 - 60mins) or end_mins > 1440
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
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        res_df.to_excel(writer, index=False, sheet_name='Sheet1')
                        
                    st.download_button(
                        label="📥 Download Results as Excel",
                        data=buffer,
                        file_name="missing_av7_report.xlsx",
                        mime="application/vnd.ms-excel"
                    )
                else:
                    st.warning("No missing AV7s found.")
                    st.markdown("""
                    **Possible reasons:**
                    1. All gaps were larger than the "Jump Threshold" (Check Sidebar).
                    2. All gaps were marked as 'Cancelled' in your input.
                    3. The data is perfectly sequential.
                    """)
