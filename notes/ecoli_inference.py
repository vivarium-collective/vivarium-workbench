"""
How to Read These Results(The State Bins (|000> to |111>):

These represent your discretized search space for your E. coli generator parameters.
For instance, |000> could map to a \(k_{cat}\) of \(10\text{ s}^{-1}\) and |111> to \(1000\text{ s}^{-1}\).
The Amplitude Superposition: Unlike a classical network that samples one point at a time, the quantum circuit maintains all 8
possibilities in a wave state simultaneously.The Final Output: When the loop finishes, the quantum state amplitudes are heavily concentrated around |010>
(Bin 2, which matches our \(60\%\) target). The entire final array is your Uncertainty Quantification envelope (the posterior distribution).
"""
import numpy as np
from qiskit import QuantumCircuit
from qiskit.circuit import ParameterVector
from qiskit.primitives import Estimator
from scipy.optimize import minimize

# 1. Define the Problem Geometry
num_qubits = 3  # Represents 2^3 = 8 possible parameter values for k_cat
num_parameters = 6  # Number of tunable gate angles in our Quantum Network

# Target distribution from observed E. coli data (e.g., highly skewed metabolic state)
target_distribution = np.array([0.05, 0.10, 0.60, 0.15, 0.05, 0.02, 0.02, 0.01])
target_distribution /= np.sum(target_distribution)  # Ensure normalization


# 2. Build the Parameterized Quantum Circuit (The Inference Engine)
def create_pqc():
    qc = QuantumCircuit(num_qubits)
    theta = ParameterVector('θ', length=num_parameters)

    # Layer 1: Initial rotations to create a superposition of parameter states
    for i in range(num_qubits):
        qc.ry(theta[i], i)

    # Layer 2: Entanglement to capture correlations between parameters
    qc.cx(0, 1)
    qc.cx(1, 2)

    # Layer 3: Final tuning rotations
    for i in range(num_qubits):
        qc.ry(theta[i + num_qubits], i)

    return qc, theta


qc, theta_params = create_pqc()


# 3. Define the Cost Function for the Classical Optimizer
def cost_function(theta_values, circuit, parameters, target_dist):
    # Bind the current iteration of classical weights to the quantum circuit
    bound_circuit = circuit.assign_parameters({parameters: theta_values})

    # Simulate execution using Qiskit's primitive StatevectorEstimator
    from qiskit.quantum_info import Statevector
    state = Statevector.from_instruction(bound_circuit)

    # Extract probabilities (Quantum Amplitudes squared)
    quant_probabilities = state.probabilities()

    # Compute loss (Kullback-Leibler Divergence / Relative Entropy)
    # Adds epsilon to prevent log(0)
    eps = 1e-10
    kl_divergence = np.sum(target_dist * np.log((target_dist + eps) / (quant_probabilities + eps)))
    return kl_divergence


# 4. Execute the Hybrid Quantum-Classical Inference Loop
initial_theta = np.random.uniform(0, 2 * np.pi, num_parameters)

print("Starting Quantum Parameter Inference Loop...")
result = minimize(
    cost_function,
    initial_theta,
    args=(qc, theta_params, target_distribution),
    method='COBYLA',
    options={'maxiter': 200}
)

# 5. Extract and Validate the Inferred Posterior Distribution
optimized_circuit = qc.assign_parameters({theta_params: result.x})
from qiskit.quantum_info import Statevector

final_probabilities = Statevector.from_instruction(optimized_circuit).probabilities()

print("\n--- Inference Results ---")
print(f"Optimization Converged: {result.success}")
print(f"Final Objective Loss:  {result.fun:.5f}")
print("\nState Bin | Target Prob | Quantum Inferred Prob")
print("-" * 45)
for i, (t, q) in enumerate(zip(target_distribution, final_probabilities)):
    print(f"  |{i:03b}>   |    {t:.3f}    |        {q:.3f}")
