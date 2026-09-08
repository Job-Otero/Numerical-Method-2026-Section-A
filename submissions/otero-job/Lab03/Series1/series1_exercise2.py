"""
series1_exercise2.py
====================
Exercise 2: Convergence of (a^h - 1) / h toward ln(a)

This script demonstrates the convergence of the difference quotient
    (a^h - 1) / h
toward the natural logarithm ln(a) as h approaches zero.

We use three values of a:
    a = 2
    a = e
    a = 3

For each combination of a and h, we compute:
    approximation = (a^h - 1) / h
    true_value    = ln(a)
    error         = |approximation - true_value|

A convergence tolerance of 1e-6 is used to identify when the
approximation is sufficiently accurate.

The script prints a formatted table and generates a grouped bar chart
showing the approximations for each h, with dashed reference lines at
ln(2), ln(e), and ln(3).

Output: exercise2_results.png
"""

import math
import os
import numpy as np
import matplotlib.pyplot as plt

# Directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PNG = os.path.join(SCRIPT_DIR, "exercise2_results.png")

# ----------------------------------------------------------------------
# 1. Define parameters
# ----------------------------------------------------------------------
a_values = [2.0, math.e, 3.0]
a_labels = ["a = 2", "a = e", "a = 3"]

# Decreasing values of h
h_values = [0.1, 0.01, 0.001, 0.0001, 0.00001, 0.000001]

# Convergence tolerance
tolerance = 1e-6

# ----------------------------------------------------------------------
# 2. Compute approximations, true values, and errors
# ----------------------------------------------------------------------
results = {}   # results[a_index][h_index] = (approx, true, error)
for i, a in enumerate(a_values):
    results[i] = []
    for h in h_values:
        approx = (a ** h - 1.0) / h
        true = math.log(a)
        error = abs(approx - true)
        results[i].append((approx, true, error))

# ----------------------------------------------------------------------
# 3. Print a formatted table
# ----------------------------------------------------------------------
print("=" * 100)
print("Exercise 2: Convergence of (a^h - 1) / h toward ln(a)")
print("=" * 100)
print(f"Convergence tolerance: {tolerance:.1e}")
print()

header = f"{'a':>6} {'h':>12} {'(a^h - 1)/h':>20} {'ln(a)':>16} {'|error|':>14} {'Converged?':>12}"
print(header)
print("-" * 100)

for i, a in enumerate(a_values):
    for j, h in enumerate(h_values):
        approx, true, error = results[i][j]
        converged = "Yes" if error < tolerance else "No"
        print(f"{a_labels[i]:>6} {h:>12.6f} {approx:>20.12f} {true:>16.12f} "
              f"{error:>14.6e} {converged:>12}")

print("-" * 100)

# Identify when each a reaches the tolerance
for i, a in enumerate(a_values):
    for j, h in enumerate(h_values):
        approx, true, error = results[i][j]
        if error < tolerance:
            print(f"{a_labels[i]}: tolerance reached at h = {h:.6f} "
                  f"(error = {error:.6e})")
            break
    else:
        print(f"{a_labels[i]}: tolerance NOT reached within the given h values")

print("=" * 100)

# ----------------------------------------------------------------------
# 4. Create the grouped bar chart
# ----------------------------------------------------------------------
fig, ax = plt.subplots(figsize=(12, 7))

# Positions for the bars
x = np.arange(len(h_values))
width = 0.25

# Colors for the three a values
colors = ["steelblue", "seagreen", "coral"]

# Plot bars for each a value
for i, a in enumerate(a_values):
    approx_vals = [results[i][j][0] for j in range(len(h_values))]
    ax.bar(x + (i - 1) * width, approx_vals, width,
           label=a_labels[i], color=colors[i], alpha=0.85)

# Add dashed reference lines for ln(2), ln(e), ln(3)
ln_vals = [math.log(a) for a in a_values]
for i, a in enumerate(a_values):
    ax.axhline(ln_vals[i], color=colors[i], linestyle="--", linewidth=1.5,
               label=f"ln({a_labels[i].split('=')[1].strip()}) = {ln_vals[i]:.6f}")

# Formatting
ax.set_xticks(x)
ax.set_xticklabels([f"h = {h:.6f}" for h in h_values], rotation=45, ha="right")
ax.set_xlabel("h values")
ax.set_ylabel("Approximation of (a^h - 1) / h")
ax.set_title("Convergence of (a^h - 1) / h toward ln(a) for a = 2, e, 3")
ax.legend(loc="best", fontsize=9)
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(OUTPUT_PNG, dpi=150)
print(f"\nFigure saved as: {OUTPUT_PNG}")
plt.close(fig)
