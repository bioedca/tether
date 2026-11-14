# Program to plot TDPs as in manuscript. Needs a CSV file with the data.
# Data are provided in EXCEL file for each figure.
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
from scipy.ndimage import gaussian_filter
from scipy.interpolate import RectBivariateSpline

# Read data from CSV. Edit here with name of file. Extract data from
#EXCEL file and save in CSV format.
filename = 'fig3i.csv'
data = pd.read_csv(filename)

# Extract d1 and d2
d1 = data['Initial E_FRET'].values
d2 = data['Final E_FRET'].values

# Define parameters (similar to your prefs dictionary)
fret_min = -0.25
fret_max = 1.25
fret_nbins = 50
hist_smoothx = 1.0
hist_smoothy = 1.0
hist_interp_res = 800
hist_normalize = True

# Create histogram
x_edges = np.linspace(fret_min, fret_max, fret_nbins + 1)
z, x_edges, y_edges = np.histogram2d(d1, d2, bins=[x_edges, x_edges])

# Calculate midpoints
x_mid = (x_edges[:-1] + x_edges[1:]) / 2
y_mid = (y_edges[:-1] + y_edges[1:]) / 2

# Smooth histogram
z = gaussian_filter(z, (hist_smoothx, hist_smoothy))

# Interpolate histogram using RectBivariateSpline
f = RectBivariateSpline(x_mid, y_mid, z)
x_interp = np.linspace(fret_min, fret_max, hist_interp_res)
y_interp = np.linspace(fret_min, fret_max, hist_interp_res)
z_interp = f(x_interp, y_interp)
z_interp[z_interp < 0] = 0.

# Normalize histogram
if hist_normalize:
    z_interp /= z_interp.max()

# Plot
fig, ax = plt.subplots(figsize=(6, 6))
cmap = 'jet'  # Colormap

# Define color floor and ceiling
color_floor = 0.0001
color_ceiling = 0.85

# Plotting the 2D histogram
cax = ax.imshow(z_interp.T, cmap=cmap, origin='lower', interpolation='none',
                extent=[x_interp.min(), x_interp.max(), y_interp.min(), y_interp.max()],
                vmin=color_floor, vmax=color_ceiling)

# Add colorbar
cbar = fig.colorbar(cax, ax=ax, extend='neither')
cbar.set_ticks(np.linspace(color_floor, color_ceiling, 5))
cbar.set_ticklabels(["{:.3f}".format(i) for i in np.linspace(color_floor, color_ceiling, 5)])

# Set labels and titles
ax.set_xlabel(r'Initial E$_{\rm{FRET}}$')
ax.set_ylabel(r'Final E$_{\rm{FRET}}$')
ax.set_title('Transition Density Plot')

plt.show()






