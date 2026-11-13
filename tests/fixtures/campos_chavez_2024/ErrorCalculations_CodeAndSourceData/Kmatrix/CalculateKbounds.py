#  Estimate the rates and upper and lower limits from the
# unnormalized matrix produced by tMAVEN
#Input is a file containing the transition matrix from tMAVEN
# Output is the rates and errors.
import numpy as np
import sys


# ## Variance of Dirichlet
# 
# Each row of the un-normalized counts matrix is a Dirichlet distribution.  The expectation of these Dirichlet distributions are the transition probabilities.  To find the variances, we can use the following Dirichlet variance formula,
# $$
#     var[x_i] = \frac{\tilde{\alpha}_i (1 - \tilde{\alpha}_i)}{\alpha_0 + 1}
# $$
# where $\tilde{\alpha}_i = \frac{\alpha_i}{\alpha_0}$, $\alpha_i$ is the entry of the counts matrix, and $\alpha_0$ is the sum of the row $x_i$ is in.
def tm_var(counts):
    """
    Calculates the variance of each entry in the counts matrix using the Dirichlet variance formula 
    Args:
        counts: un-normalized counts matrix
    Returns: 
        var: matrix of variances, same size as counts
    """
    var = np.empty_like(counts)
    a_0 = np.sum(counts, 1)
    
    for i in range(counts.shape[0]):
        for j in range(counts.shape[1]):
            a_tilde = counts[i][j] / a_0[i]
            var[i][j] = (a_tilde * (1 - a_tilde)) / (a_0[i] + 1)

    return var

# ## Variance of Rate Matrix
# 
# We can calculate an upper and lower bound on the rate matrix, $k$, by using the following relation between transition probabilities and rate constants,
# $$
#     k = -\frac{ln(1-p)}{dt}.
# $$
# _Note: We calculate a separate upper and lower bound on the rates rather than reporting a symmetric value because log is asymmetric (nonlinear)._
def k_bounds(counts, var, dt):
    """
    Calculates bounds for each entry in the rate matrix
    Args:
        counts: un-normalized counts matrix
        var: Dirichlet variance of counts matrix
        dt: frame rate of data acquisition
    Returns: 
        k_upper, k_lower: matrices of upper and lower bounds respectively, same size as counts
    """
     tm = np.empty_like(counts)
    for i in range(counts.shape[0]):
        tm[i] = counts[i] / np.sum(counts, 1)[i]

    print("\nNormalized transition matrix =\n")
    with np.printoptions(precision=6, suppress=True, formatter={'float': '{:0.5f}'.format}, linewidth=72):
        print(tm)
    # just printing the rate so we can see the bounds (you can remove this)
    print("\nRates = \n")
    rates = -np.log(1 - tm) / dt
    with np.printoptions(precision=6, suppress=True, formatter={'float': '{:0.5f}'.format}, linewidth=72):
        print(rates)
    p_upper = tm + np.sqrt(var)
    p_lower = tm - np.sqrt(var)
    k_upper = -np.log(1 - p_upper) / dt
    k_lower = -np.log(1 - p_lower) / dt
    return k_upper, k_lower,rates
#
INFILE=sys.argv[1]
MATRIX=open(INFILE,"r")
TransMatrix=np.loadtxt(MATRIX)
#
print("File = ",INFILE)
print("\nTransition matrix = \n")
with np.printoptions(precision=6, suppress=True, formatter={'float': '{:0.5f}'.format}, linewidth=72):
 print(TransMatrix)

var = tm_var(TransMatrix)
print("\nVariance matrix = \n")
with np.printoptions(precision=12, suppress=True, formatter={'float': '{:0.12f}'.format}, linewidth=72):
 print(var)

#   We collected data at 10 frames per second (100 ms frames). DT = 0.1
# Change last entry to whatever value you used.
Upper,Lower,rates=k_bounds(TransMatrix, var, .1)

print("\nUpper bounds \n")
with np.printoptions(precision=6, suppress=True, formatter={'float': '{:0.5f}'.format}, linewidth=72):
 print(Upper)
print("\nLower bounds \n")
with np.printoptions(precision=6, suppress=True, formatter={'float': '{:0.5f}'.format}, linewidth=72):
 print(Lower)
#
UpDif = Upper - rates
LowDif = Lower - rates
print("\nUpper bounds difference\n")
with np.printoptions(precision=6, suppress=True, formatter={'float': '{:0.5f}'.format}, linewidth=72):
 print(UpDif)
print("\nLower bounds difference \n")
with np.printoptions(precision=6, suppress=True, formatter={'float': '{:0.5f}'.format}, linewidth=72):
 print(LowDif)

print("\n")




