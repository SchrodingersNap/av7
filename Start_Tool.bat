@echo off
TITLE AV7 Gap Analyzer Server
CLS

ECHO ========================================================
ECHO          STARTING AV7 ANALYSIS SERVER...
ECHO ========================================================
ECHO.

:: 1. Get the local IP Address (IPv4)
FOR /F "tokens=14" %%a IN ('ipconfig ^| findstr "IPv4"') DO SET IP=%%a

:: 2. Display Instructions
ECHO --------------------------------------------------------
ECHO  SUCCESS! The tool is running.
ECHO.
ECHO  1. YOU access it here:      http://localhost:8501
ECHO  2. OTHERS access it here:   http://%IP%:8501
ECHO.
ECHO  (Send the link in step #2 to your colleagues)
ECHO --------------------------------------------------------
ECHO.
ECHO  DO NOT CLOSE THIS WINDOW while the tool is being used.
ECHO.

:: 3. Run the Streamlit App
:: Ensure 'app.py' is in the same folder as this script
streamlit run app.py --server.address 0.0.0.0

PAUSE