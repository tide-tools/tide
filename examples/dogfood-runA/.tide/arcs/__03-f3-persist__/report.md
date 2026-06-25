# report — f3-persist
contract: f3-persist
accepted: yes

Added localStorage persistence to index.html (key tidePoolA, save shape {plankton,autoLevel,clickLevel,savedAt}). save() on every click/buy + 15s flush + beforeunload; load() restores plankton+levels with field validation and repaints; offline progress = floor(autoRate*min(elapsedSec,8h)) shown via toast, re-baselined post-boot to avoid double-count; confirm-gated Reset button wipes save and resets to fresh. f1 click loop + f2 shop preserved, single-file zero-dep.
