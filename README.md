# Astrophotography Mount Watchdog
## Overview
Using indi library APIs, this bash script uses the RA stepper motor values to determine the absolute position of a mount. The user may then set specific values for the east and west side of the pier to prevent the mount from crashing into the pier. If the mount exceeds (or goes below) these values, the script will send an abort to the mount. Most indi software clients recognize that the abort has occurred and stop further actions. Users will want to ensure that their software behaves properly before deploying in the field.

## Usage
This script must be configured using the -configure option and the user then determines what is the max allowed values for both the east and west piers. Moving the telescope on the mount by releasing the RA clutch will invalidate these settings unless the telescope is returned to its original position.
