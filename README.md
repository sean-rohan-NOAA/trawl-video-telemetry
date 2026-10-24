# Trawl Video-Telemetry Viewer

**Trawl Video-Telemetry Viewer** is a synchronized playback and multi-stream telemetry visualization tool designed for marine fisheries and trawl gear surveys. Developed for the NOAA Alaska Fisheries Science Center (AFSC), this application allows researchers to seamlessly align underwater video footage with sensor data, annotate events, and extract time-stamped annotations and video metadata.

## Features

* **Synchronized Playback:** View video footage alongside interactive telemetry data plots. The plot's vertical timeline scrubber syncs exactly with the video playback.

* **Interactive Telemetry:** Click anywhere on the telemetry plots to instantly seek to that exact moment in the video.

* **Timestamped Annotations:** Log specific events with timestamped comments. Double-click a comment to jump right back to that point in the video.

* **Video Catalog Management:** Scan directories for `.MOV`/`.MP4` files to build a local SQLite video catalog.

* **Smart Metadata Extraction:** Features a custom pure-Python QuickTime atom parser to extract true creation timestamps without needing external dependencies like FFmpeg.

* **Time Offset Calibration:** Easily correct clock drift between cameras and telemetry sensors by applying precise time offsets (in seconds).

* **Frame Capture:** Export paused video frames as PNGs with timestamped filenames for reports or presentations.

* **Dynamic Timezones:** Toggle between global timezones (e.g., UTC, PST, AKST) on the fly, instantly updating plots and catalogs.

* **Data Export/Import:** Export video catalogs and timestamped comments to CSV or TXT formats for external analysis.

## Prerequisites & Dependencies

This application requires Python 3.9 or higher.

Install the required Python packages using pip:

```
pip install PyQt6 pyqtgraph pandas numpy opencv-python

```

*Note: `opencv-python` (cv2) is technically optional but **highly recommended**. It is required for timeline hover-thumbnails and ensures exact-frame PNG screen captures.*

## Installation

1. Clone the repository:

   ```
   git clone https://github.com/yourusername/trawl-sync-viewer.git
   cd trawl-sync-viewer
   
   ```

2. Install dependencies (see above).

3. Run the application:

   ```
   python trawlview.py
   ```

## Usage Guide

### 1. Building the Video Catalog

* Go to **File > Scan Directory for .MOV Files...**

* The app will scan the folder, extract metadata, and populate the *Video Catalog* pane on the left.

* You can select any video in the list to edit its Haul, Position, Description, and apply time offsets.

### 2. Loading Telemetry Data

* Go to **Import > Load Telemetry SQLite Database...**

* Select your SQLite database (see data format requirements below).

* The **Column Selector** will appear. Choose which sensor data columns you want to be available in the plot dropdowns.

### 3. Playback & Synchronization

* Double-click a video in the catalog to load it into the player.

* Use the dropdowns above each plot to select which data streams to visualize (e.g., Depth, Temperature, Pitch, Roll).

* Click **Play** or scrub through the timeline. Watch the red indicator line track across the data plots.

### 4. Logging Observations

* Type an observation into the comment box in the bottom right and press **Enter** (or click **Add Comment**).

* The comment is saved to the database with the exact video position and real-world timestamp.

## Telemetry Database Format Requirements

To load sensor data into the application, your SQLite database must follow a specific schema:

1. **Table Name:** The data must be stored in a table named `trawl_data`.

2. **Timestamp Column:** The table must contain a column named `DT`. This column should contain either:

   * String-based datetimes (parseable by pandas).

   * Unix epoch timestamps (seconds or milliseconds).

3. **Data Columns:** Additional numeric columns represent your telemetry streams (e.g., `Depth_m`, `Temp_C`, `Altitude`, etc.).

**Example SQLite Schema:**

```
CREATE TABLE trawl_data (
    DT TEXT,
    Depth_m REAL,
    Temp_C REAL,
    Pitch REAL,
    Roll REAL
);

```

## Author

**Sean Rohan**

NOAA Alaska Fisheries Science Center (AFSC)

Email: sean.rohan@noaa.gov

## License

This repository is a software product and is not official communication of the National Oceanic and Atmospheric Administration (NOAA), or the United States Department of Commerce (DOC). All NOAA GitHub project code is provided on an 'as is' basis and the user assumes responsibility for its use. Any claims against the DOC or DOC bureaus stemming from the use of this GitHub project will be governed by all applicable Federal law. Any reference to specific commercial products, processes, or services by service mark, trademark, manufacturer, or otherwise, does not constitute or imply their endorsement, recommendation, or favoring by the DOC. The DOC seal and logo, or the seal and logo of a DOC bureau, shall not be used in any manner to imply endorsement of any commercial product or activity by the DOC or the United States Government.