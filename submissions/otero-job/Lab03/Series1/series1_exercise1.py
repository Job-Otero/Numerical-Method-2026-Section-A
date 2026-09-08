"""
series1_exercise1.py
====================
Exercise 1: Convergence of (1 + 1/n)^n toward e

This script demonstrates the convergence of the sequence
    a_n = (1 + 1/n)^n
toward the mathematical constant e as n increases.

For each value of n, we compute:
    value = (1 + 1/n)^n
    error = |value - e|

The script prints a formatted numerical table and generates a figure
with two subplots:
    Graph 1: Bar chart of (1 + 1/n)^n with a dashed line at e.
    Graph 2: Absolute error versus n on a logarithmic scale.

Output: exercise1_results.png
"""

import math
import os
import numpy as np
import matplotlib.pyplot as plt

# Directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PNG = os.path.join(SCRIPT_DIR, "exercise1_results.png")

# ----------------------------------------------------------------------
# 1. Compute the sequence values
# ----------------------------------------------------------------------
# Start with n = 1, 2, 4 and keep doubling to demonstrate convergence.
n_values = []
n = 1
while n <= 1_048_576:          # 2^20 — sufficiently large to show convergence
    n_values.append(n)
    n *= 2

# True value of e
e_true = math.e

# Compute the sequence and the absolute error for each n
values = []
errors = []
for n in n_values:
    val = (1.0 + 1.0 / n) ** n
    values.append(val)
    errors.append(abs(val - e_true))

# ----------------------------------------------------------------------
# 2. Print a formatted numerical table
# ----------------------------------------------------------------------
print("=" * 70)
print("Exercise 1: Convergence of (1 + 1/n)^n toward e")
print("=" * 70)
print(f"{'n':>12} {'(1 + 1/n)^n':>20} {'|value - e|':>20}")
print("-" * 70)
for n, val, err in zip(n_values, values, errors):
    print(f"{n:>12,} {val:>20.12f} {err:>20.12e}")
print("-" * 70)
print(f"e (true value) = {e_true:.15f}")
print(f"Final n        = {n_values[-1]:,}")
print(f"Final value    = {values[-1]:.15f}")
print(f"Final error    = {errors[-1]:.6e}")
print("=" * 70)

# ----------------------------------------------------------------------
# 3. Create the figure with two subplots
# ----------------------------------------------------------------------
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

# --- Graph 1: Bar chart of (1 + 1/n)^n ---
# Use a subset of n values for the bar chart so it remains readable.
bar_n = n_values[:12]
bar_vals = values[:12]
x_pos = np.arange(len(bar_n))

bars = ax1.bar(x_pos, bar_vals, color="steelblue", alpha=0.8,
               label=r"$(1 + 1/n)^n$")
ax1.axhline(e_true, color="red", linestyle="--", linewidth=2,
            label=f"e = {e_true:.6f}")
ax1.set_xticks(x_pos)
ax1.set_xticklabels([f"{v:,}" for v in bar_n], rotation=45, ha="right")
ax1.set_xlabel("n")
ax1.set_ylabel("Calculated value")
ax1.set_title("Convergence of $(1 + 1/n)^n$ toward e")
ax1.legend()
ax1.grid(True, alpha=0.3)

# --- Graph 2: Error versus n (logarithmic scale) ---
ax2.plot(n_values, errors, marker="o", linestyle="-", color="darkorange",
         label="Absolute error")
ax2.set_xscale("log")
ax2.set_yscale("log")
ax2.set_xlabel("n (log scale)")
ax2.set_ylabel("Absolute error (log scale)")
ax2.set_title("Error of $(1 + 1/n)^n$ versus n")
ax2.legend()
ax2.grid(True, which="both", alpha=0.3)

plt.tight_layout()
plt.savefig(OUTPUT_PNG, dpi=150)
print(f"\nFigure saved as: {OUTPUT_PNG}")
plt.close(fig)
