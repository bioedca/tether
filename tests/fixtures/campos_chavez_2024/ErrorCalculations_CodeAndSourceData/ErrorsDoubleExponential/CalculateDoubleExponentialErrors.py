#
#  Calculate tau and tau error from parameters obtained from fitting a double exponential.
# Input is an archive with the 4 parameters and their errors
# in the order A1val,A1error,A2val,A2error,K1val,K1error,K2val,K2error 
import sympy as sp
import numpy as np
import sys

# Define the symbolic variables
A1,A2,K1,K2 = sp.symbols('A1 A2 K1 K2', real=True)

# Define the function
#tau = (1./K1)*(A1/(A1+A2)) + (1./K2)*(A2/(A1+A2))
tau = (A1/K1 + A2/K2)/(A1+A2)

# Calculate the partial derivatives of tau with respect to A1,A2,K1,K2
dtau_dA1 = sp.diff(tau, A1)
dtau_dA2 = sp.diff(tau, A2)
dtau_dK1 = sp.diff(tau, K1)
dtau_dK2 = sp.diff(tau, K2)
#
INFILE=sys.argv[1]
DATA=open(INFILE,"r")
A1val,A1error,A2val,A2error,K1val,K1error,K2val,K2error=np.loadtxt(DATA)
print("Input data =",A1val,A1error,A2val,A2error,K1val,K1error,K2val,K2error)
# 
Tau = tau.evalf(subs={A1: A1val, A2: A2val, K1: K1val, K2: K2val})

# Calculate the uncertainty in Tau using error propagation
tau_uncertainty = sp.sqrt(
    (dtau_dA1.evalf(subs={A1: A1val, A2: A2val, K1: K1val, K2: K2val}) * A1error)**2 +
    (dtau_dA2.evalf(subs={A1: A1val, A2: A2val, K1: K1val, K2: K2val}) * A2error)**2 +
    (dtau_dK1.evalf(subs={A1: A1val, A2: A2val, K1: K1val, K2: K2val}) * K1error)**2 +
    (dtau_dK2.evalf(subs={A1: A1val, A2: A2val, K1: K1val, K2: K2val}) * K2error)**2
)

# Print the results 
print("Error calculations:")
print(f"tau value: {Tau:.2f}")
print(f"Uncertainty in tau: {tau_uncertainty:.2f}")
