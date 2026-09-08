"""
series1_exercise3.py
====================
Exercise 3: Approximation of e^x using the power series

This script approximates e^x using the Taylor/Maclaurin series:
    e^x = Σ (x^n / n!)   for n = 0, 1, 2, ...

We compute partial sums S_N = Σ_{n=0}^{N} (x^n / n!) for increasing N.

To avoid numerical overflow from computing huge factorials directly,
we use the stable recurrence relation:
    term_{n+1} = term_n * x / (n + 1)
    partial_sum = partial_sum + term

The script:
    1. Computes the power series approximation for increasing N.
    2. Compares each approximation with math.exp(x).
    3. Calculates absolute errors.
    4. Continues the iteration up to N = 10,000.
    5. Determines how many terms are needed for useful accuracy.
    6. Prints important numerical results.
    7. Creates a figure with two subplots:
        Graph 1: Bar chart of selected partial sums with a dashed line at e^x.
        Graph 2: Error versus number of terms (logarithmic scale).

Output: exercise3_results.png
"""

import math
import os
import numpy as np
import matplotlib.pyplot as plt

# Directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PNG = os.path.join(SCRIPT_DIR, "exercise3_results.png")

# ----------------------------------------------------------------------
# 1. Define parameters
# ----------------------------------------------------------------------
x = 2.0                     # Value of x for e^x
true_value = math.exp(x)    # True value of e^x
max_N = 10_000              # Maximum number of terms

print("=" * 70)
print("Exercise 3: Approximation of e^x using the power series")
print("=" * 70)
print(f"x = {x}")
print(f"True value of e^{x} = {true_value:.15f}")
print(f"Maximum number of terms N = {max_N}")
print()

# ----------------------------------------------------------------------
# 2. Compute partial sums using the stable recurrence relation
# ----------------------------------------------------------------------
# We will store selected partial sums and their errors for plotting.
selected_N = [0, 1, 2, 3, 4, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000]
selected_sums = []
selected_errors = []

# Track all errors to find when useful accuracy is achieved
all_errors = []

# Initialize the series
term = 1.0          # term_0 = x^0 / 0! = 1
partial_sum = 1.0   # S_0 = 1
all_errors.append(abs(partial_sum - true_value))

# Track when we first reach various accuracy levels
accuracy_levels = {1e-2: None, 1e-4: None, 1e-6: None, 1e-8: None, 1e-10: None}

# Iterate from n = 1 to max_N
for n in range(1, max_N + 1):
    # Recurrence: term_n = term_{n-1} * x / n
    term = term * x / n
    partial_sum += term

    error = abs(partial_sum - true_value)
    all_errors.append(error)

    # Record when each accuracy level is first achieved
    for level in accuracy_levels:
        if accuracy_levels[level] is None and error < level:
            accuracy_levels[level] = n

# Collect selected partial sums and errors
for N in selected_N:
    # Recompute partial sum up to N (or use stored values)
    # For efficiency, we recompute using the recurrence up to N.
    term = 1.0
    s = 1.0
    for n in range(1, N + 1):
        term = term * x / n
        s += term
    selected_sums.append(s)
    selected_errors.append(abs(s - true_value))

# ----------------------------------------------------------------------
# 3. Print important numerical results
# ----------------------------------------------------------------------
print("Selected partial sums:")
print(f"{'N':>8} {'S_N':>20} {'|S_N - e^x|':>20}")
print("-" * 55)
for N, s, err in zip(selected_N, selected_sums, selected_errors):
    print(f"{N:>8,} {s:>20.12f} {err:>20.6e}")
print("-" * 55)

print("\nAccuracy levels achieved:")
for level, n_reached in accuracy_levels.items():
    if n_reached is not None:
        print(f"  Error < {level:.0e} first achieved at N = {n_reached}")
    else:
        print(f"  Error < {level:.0e} NOT achieved within N = {max_N}")

print(f"\nFinal partial sum S_{max_N} = {partial_sum:.15f}")
print(f"True value e^{x} = {true_value:.15f}")
print(f"Final absolute error = {abs(partial_sum - true_value):.6e}")
print("=" * 70)

# ----------------------------------------------------------------------
# 4. Create the figure with two subplots
# ----------------------------------------------------------------------
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

# --- Graph 1: Bar chart of selected partial sums ---
x_pos = np.arange(len(selected_N))
bars = ax1.bar(x_pos, selected_sums, color="mediumpurple", alpha=0.85,
               label=f"Partial sum S_N")
ax1.axhline(true_value, color="red", linestyle="--", linewidth=2,
            label=f"e^{x} = {true_value:.6f}")
ax1.set_xticks(x_pos)
ax1.set_xticklabels([f"{v:,}" for v in selected_N], rotation=45, ha="right")
ax1.set_xlabel("Number of terms N")
ax1.set_ylabel("Approximation of e^x")
ax1.set_title(f"Partial sums of e^{x} power series")
ax1.legend()
ax1.grid(True, alpha=0.3)

# --- Graph 2: Error versus number of terms (logarithmic scale) ---
# Use a subset of N values for the error plot to keep it readable.
plot_N = np.arange(0, max_N + 1, 10)  # every 10th term
plot_errors = [all_errors[n] for n in plot_N]

ax2.plot(plot_N, plot_errors, marker=".", linestyle="-", color="darkgreen",
         label="Absolute error")
ax2.set_yscale("log")
ax2.set_xlabel("Number of terms N")
ax2.set_ylabel("Absolute error (log scale)")
ax2.set_title(f"Error of e^{x} power series approximation")
ax2.legend()
ax2.grid(True, which="both", alpha=0.3)

plt.tight_layout()
plt.savefig(OUTPUT_PNG, dpi=150)
print(f"\nFigure saved as: {OUTPUT_PNG}")
plt.close(fig)
