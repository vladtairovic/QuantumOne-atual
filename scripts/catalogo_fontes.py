"""Gera documentos/fontes_conhecimento.json a partir de listas curadas.

Fluxo administrativo (nenhum passo roda durante consultas):
1. ``python scripts/catalogo_fontes.py --candidatos`` grava todas as fontes candidatas.
2. ``python -m rag.sincronizar_conhecimento --verificar-catalogo docs/catalogo-verificacao.json``
   baixa e extrai cada URL (robots.txt e crawl-delay respeitados), sem indexar.
3. ``python scripts/catalogo_fontes.py --aplicar docs/catalogo-verificacao.json``
   mantém no catálogo somente as fontes que foram baixadas e extraídas com sucesso.

As URLs são de documentação técnica pública. Os snapshots ficam no volume de
dados da API (``KNOWLEDGE_PATH``), não no repositório: a redistribuição depende
da licença de cada fonte, registrada no campo ``licenca``.
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGO = ROOT / "documentos" / "fontes_conhecimento.json"

IBM = "https://quantum.cloud.ibm.com/docs/en"
IBM_LIC = "IBM Quantum Documentation; conteúdo do Qiskit sob Apache-2.0, consultar termos do site"

# (slug, temas, nivel)
IBM_GUIDES = [
    ("circuit-library", ["circuitos", "biblioteca"], "intermediario"),
    ("construct-circuits", ["circuitos", "qiskit"], "basico"),
    ("visualize-circuits", ["circuitos", "visualizacao"], "basico"),
    ("bit-ordering", ["fundamentos", "medicao", "ordem_de_bits"], "basico"),
    ("measure-qubits", ["medicao"], "basico"),
    ("simulate-with-qiskit-sdk-primitives", ["simulacao", "primitivas"], "intermediario"),
    ("transpile", ["transpilacao"], "intermediario"),
    ("transpiler-stages", ["transpilacao", "roteamento", "otimizacao"], "avancado"),
    ("set-optimization", ["transpilacao", "otimizacao"], "intermediario"),
    ("defaults-and-configuration-options", ["transpilacao"], "intermediario"),
    ("classical-feedforward-and-control-flow", ["controle_classico", "circuitos_dinamicos"], "avancado"),
    ("install-qiskit", ["instalacao"], "basico"),
    ("synthesize-unitary-operators", ["sintese", "unitarias"], "avancado"),
    ("operators-overview", ["operadores", "quantum_info"], "intermediario"),
    ("specify-observables-pauli", ["observaveis", "pauli"], "intermediario"),
    ("operator-class", ["operadores", "quantum_info"], "intermediario"),
    ("intro-to-patterns", ["fluxo_de_trabalho"], "basico"),
    ("map-problem-to-circuits", ["fluxo_de_trabalho", "circuitos"], "intermediario"),
    ("transpile-with-pass-managers", ["transpilacao", "passmanager"], "avancado"),
    ("custom-transpiler-pass", ["transpilacao", "passmanager"], "avancado"),
    ("create-transpiler-plugin", ["transpilacao", "plugins"], "avancado"),
    ("transpiler-plugins", ["transpilacao", "plugins"], "avancado"),
    ("dynamical-decoupling-pass-manager", ["transpilacao", "mitigacao"], "avancado"),
    ("represent-quantum-computers", ["hardware", "target"], "avancado"),
    ("custom-backend", ["hardware", "target"], "avancado"),
    ("common-parameters", ["transpilacao"], "intermediario"),
    ("primitives", ["primitivas"], "intermediario"),
    ("get-started-with-primitives", ["primitivas"], "basico"),
    ("primitive-input-output", ["primitivas"], "intermediario"),
    ("simulate-with-qiskit-aer", ["simulacao", "aer"], "intermediario"),
    ("local-testing-mode", ["simulacao", "runtime"], "intermediario"),
    ("simulate-stabilizer-circuits", ["simulacao", "estabilizadores", "clifford"], "avancado"),
    ("build-noise-models", ["ruido", "simulacao"], "avancado"),
    ("plot-quantum-states", ["visualizacao", "estados"], "basico"),
    ("save-circuits", ["circuitos", "serializacao"], "basico"),
    ("interoperate-qiskit-qasm2", ["openqasm2", "interoperabilidade"], "intermediario"),
    ("interoperate-qiskit-qasm3", ["openqasm3", "interoperabilidade"], "intermediario"),
    ("introduction-to-qasm", ["openqasm"], "basico"),
    ("error-mitigation-and-suppression-techniques", ["mitigacao", "ruido"], "avancado"),
    ("configure-error-mitigation", ["mitigacao"], "avancado"),
    ("configure-error-suppression", ["mitigacao", "supressao"], "avancado"),
    ("noise-learning", ["ruido", "mitigacao"], "avancado"),
    ("debugging-tools", ["depuracao"], "intermediario"),
    ("qiskit-transpiler-service", ["transpilacao", "servico"], "avancado"),
    ("ai-transpiler-passes", ["transpilacao", "roteamento"], "avancado"),
    ("parametric-compilation", ["circuitos_parametrizados", "transpilacao"], "avancado"),
    ("execution-modes", ["runtime", "execucao"], "intermediario"),
    ("monitor-job", ["runtime", "execucao"], "basico"),
    ("estimate-job-run-time", ["runtime", "execucao"], "intermediario"),
    ("pulse", ["hardware"], "avancado"),
    ("dynamic-circuits-considerations", ["circuitos_dinamicos"], "avancado"),
    ("measure-into-variables", ["circuitos_dinamicos", "medicao"], "avancado"),
    ("qiskit-addons-utils", ["addons"], "avancado"),
    ("qiskit-addons-cutting", ["addons", "corte_de_circuitos"], "avancado"),
    ("qiskit-addons-mpf", ["addons", "simulacao_hamiltoniana"], "avancado"),
    ("qiskit-addons-obp", ["addons", "propagacao_de_operadores"], "avancado"),
    ("qiskit-addons-sqd", ["addons", "quimica_quantica"], "avancado"),
    ("qiskit-addons-aqc", ["addons", "compilacao_aproximada"], "avancado"),
    ("qiskit-code-assistant", ["ferramentas"], "basico"),
    ("algorithmiq-tem", ["mitigacao", "funcoes"], "avancado"),
    ("functions", ["funcoes"], "intermediario"),
    ("latest-updates", ["atualizacoes"], "basico"),
]

IBM_API = [
    # (módulo/classe, temas, nivel)
    ("circuit", ["circuitos"], "intermediario"),
    ("qiskit.circuit.QuantumCircuit", ["circuitos"], "intermediario"),
    ("qiskit.circuit.Parameter", ["circuitos_parametrizados"], "intermediario"),
    ("qiskit.circuit.ParameterVector", ["circuitos_parametrizados"], "intermediario"),
    ("qiskit.circuit.QuantumRegister", ["circuitos", "registradores"], "basico"),
    ("qiskit.circuit.ClassicalRegister", ["circuitos", "registradores"], "basico"),
    ("qiskit.circuit.Instruction", ["circuitos"], "avancado"),
    ("qiskit.circuit.Gate", ["circuitos", "portas"], "intermediario"),
    ("qiskit.circuit.ControlledGate", ["circuitos", "portas"], "avancado"),
    ("qiskit.circuit.IfElseOp", ["controle_classico"], "avancado"),
    ("circuit_library", ["biblioteca", "portas"], "intermediario"),
    ("circuit_classical", ["controle_classico"], "avancado"),
    ("qiskit.circuit.library.QFT", ["algoritmos", "qft"], "intermediario"),
    ("qiskit.circuit.library.QFTGate", ["algoritmos", "qft"], "intermediario"),
    ("qiskit.circuit.library.GroverOperator", ["algoritmos", "grover"], "intermediario"),
    ("qiskit.circuit.library.PhaseEstimation", ["algoritmos", "estimativa_de_fase"], "avancado"),
    ("qiskit.circuit.library.n_local", ["variacional", "ansatz"], "avancado"),
    ("qiskit.circuit.library.efficient_su2", ["variacional", "ansatz"], "avancado"),
    ("qiskit.circuit.library.real_amplitudes", ["variacional", "ansatz"], "avancado"),
    ("qiskit.circuit.library.QAOAAnsatz", ["qaoa", "variacional"], "avancado"),
    ("qiskit.circuit.library.UnitaryGate", ["unitarias", "portas"], "intermediario"),
    ("qiskit.circuit.library.HGate", ["portas"], "basico"),
    ("qiskit.circuit.library.CXGate", ["portas"], "basico"),
    ("qiskit.circuit.library.RZGate", ["portas", "rotacoes"], "basico"),
    ("qiskit.circuit.library.UGate", ["portas", "rotacoes"], "intermediario"),
    ("qiskit.circuit.library.SwapGate", ["portas", "roteamento"], "basico"),
    ("qiskit.circuit.library.MCXGate", ["portas"], "avancado"),
    ("quantum_info", ["quantum_info"], "intermediario"),
    ("qiskit.quantum_info.Statevector", ["estados", "simulacao"], "intermediario"),
    ("qiskit.quantum_info.Operator", ["operadores", "equivalencia"], "intermediario"),
    ("qiskit.quantum_info.DensityMatrix", ["estados", "ruido"], "avancado"),
    ("qiskit.quantum_info.SparsePauliOp", ["observaveis", "pauli"], "intermediario"),
    ("qiskit.quantum_info.Pauli", ["pauli"], "intermediario"),
    ("qiskit.quantum_info.Clifford", ["clifford", "estabilizadores"], "avancado"),
    ("qiskit.quantum_info.StabilizerState", ["estabilizadores", "simulacao"], "avancado"),
    ("qiskit.quantum_info.random_unitary", ["unitarias"], "intermediario"),
    ("qiskit.quantum_info.process_fidelity", ["equivalencia", "fidelidade"], "avancado"),
    ("qiskit.quantum_info.state_fidelity", ["fidelidade", "estados"], "intermediario"),
    ("transpiler", ["transpilacao"], "avancado"),
    ("transpiler_passes", ["transpilacao", "otimizacao"], "avancado"),
    ("transpiler_preset", ["transpilacao"], "avancado"),
    ("transpiler_synthesis", ["sintese"], "avancado"),
    ("qiskit.compiler.transpile", ["transpilacao"], "intermediario"),
    ("qiskit.transpiler.generate_preset_pass_manager", ["transpilacao", "passmanager"], "intermediario"),
    ("qiskit.transpiler.PassManager", ["transpilacao", "passmanager"], "avancado"),
    ("qiskit.transpiler.StagedPassManager", ["transpilacao", "passmanager"], "avancado"),
    ("qiskit.transpiler.CouplingMap", ["hardware", "roteamento"], "intermediario"),
    ("qiskit.transpiler.Target", ["hardware", "target"], "avancado"),
    ("qiskit.transpiler.Layout", ["layout", "roteamento"], "avancado"),
    ("qiskit.transpiler.TranspileLayout", ["layout", "roteamento"], "avancado"),
    ("qiskit.transpiler.passes.SabreLayout", ["layout", "roteamento"], "avancado"),
    ("qiskit.transpiler.passes.SabreSwap", ["roteamento"], "avancado"),
    ("qiskit.transpiler.passes.BasisTranslator", ["transpilacao", "decomposicao"], "avancado"),
    ("qiskit.transpiler.passes.Optimize1qGatesDecomposition", ["otimizacao"], "avancado"),
    ("qiskit.transpiler.passes.CommutativeCancellation", ["otimizacao", "comutacao"], "avancado"),
    ("qiskit.transpiler.passes.InverseCancellation", ["otimizacao"], "avancado"),
    ("qiskit.transpiler.passes.ConsolidateBlocks", ["otimizacao", "sintese"], "avancado"),
    ("qiskit.transpiler.passes.UnitarySynthesis", ["sintese"], "avancado"),
    ("qiskit.transpiler.passes.RemoveDiagonalGatesBeforeMeasure", ["otimizacao", "medicao"], "avancado"),
    ("qiskit.transpiler.passes.RemoveResetInZeroState", ["otimizacao"], "avancado"),
    ("qiskit.transpiler.passes.ElidePermutations", ["otimizacao", "roteamento"], "avancado"),
    ("qiskit.transpiler.passes.HighLevelSynthesis", ["sintese"], "avancado"),
    ("qiskit.transpiler.passes.Collect2qBlocks", ["otimizacao"], "avancado"),
    ("qiskit.transpiler.passes.VF2Layout", ["layout"], "avancado"),
    ("synthesis", ["sintese"], "avancado"),
    ("qasm2", ["openqasm2"], "intermediario"),
    ("qasm3", ["openqasm3"], "intermediario"),
    ("primitives", ["primitivas"], "intermediario"),
    ("qiskit.primitives.StatevectorSampler", ["primitivas", "simulacao"], "intermediario"),
    ("qiskit.primitives.StatevectorEstimator", ["primitivas", "simulacao"], "intermediario"),
    ("providers_basic_provider", ["simulacao"], "intermediario"),
    ("qiskit.providers.basic_provider.BasicSimulator", ["simulacao"], "intermediario"),
    ("converters", ["dag", "conversao"], "avancado"),
    ("dagcircuit", ["dag"], "avancado"),
    ("visualization", ["visualizacao"], "basico"),
    ("qiskit.visualization.plot_histogram", ["visualizacao", "medicao"], "basico"),
    ("result", ["resultados"], "intermediario"),
    ("exceptions", ["depuracao", "erros"], "intermediario"),
    ("circuit_annotation", ["circuitos"], "avancado"),
]

IBM_MIGRATION = [
    ("qiskit-1.0-features", ["migracao"], "intermediario"),
    ("qiskit-1.0-installation", ["migracao", "instalacao"], "basico"),
    ("qiskit-2.0", ["migracao"], "intermediario"),
    ("qiskit-execute", ["migracao", "execucao"], "intermediario"),
    ("qiskit-quantum-instance", ["migracao", "primitivas"], "intermediario"),
    ("qiskit-opflow-module", ["migracao", "operadores"], "intermediario"),
    ("qiskit-algorithms-module", ["migracao", "algoritmos"], "intermediario"),
    ("v2-primitives", ["migracao", "primitivas"], "intermediario"),
    ("qiskit-runtime", ["migracao", "runtime"], "intermediario"),
]

IBM_TUTORIALS = [
    ("hello-world", ["introducao", "emaranhamento"], "basico"),
    ("quantum-approximate-optimization-algorithm", ["qaoa", "otimizacao_combinatoria"], "avancado"),
    ("variational-quantum-eigensolver", ["vqe", "variacional"], "avancado"),
    ("grovers-algorithm", ["grover", "algoritmos"], "intermediario"),
    ("chsh-inequality", ["emaranhamento", "fundamentos"], "intermediario"),
    ("repetition-codes", ["correcao_de_erros"], "avancado"),
    ("transpilation-optimizations-with-sabre", ["roteamento", "transpilacao"], "avancado"),
    ("long-range-entanglement", ["circuitos_dinamicos", "emaranhamento"], "avancado"),
    ("quantum-phase-estimation-qctrl", ["estimativa_de_fase"], "avancado"),
    ("krylov-quantum-diagonalization", ["simulacao_hamiltoniana"], "avancado"),
    ("sample-based-quantum-diagonalization", ["quimica_quantica"], "avancado"),
    ("advanced-techniques-for-qaoa", ["qaoa"], "avancado"),
    ("real-time-benchmarking-for-qubit-selection", ["hardware"], "avancado"),
    ("error-mitigation-with-qiskit-functions", ["mitigacao"], "avancado"),
    ("compilation-methods-for-hamiltonian-simulation-circuits", ["simulacao_hamiltoniana", "transpilacao"], "avancado"),
    ("approximate-quantum-compilation-for-time-evolution", ["compilacao_aproximada"], "avancado"),
    ("circuit-cutting-for-depth-reduction", ["corte_de_circuitos"], "avancado"),
    ("wire-cutting", ["corte_de_circuitos"], "avancado"),
    ("operator-back-propagation", ["propagacao_de_operadores"], "avancado"),
    ("multi-product-formula", ["simulacao_hamiltoniana"], "avancado"),
    ("quantum-kernel-training", ["aprendizado_de_maquina"], "avancado"),
    ("combine-error-mitigation-techniques", ["mitigacao"], "avancado"),
    ("nishimori-phase-transition", ["circuitos_dinamicos"], "avancado"),
    ("pauli-correlation-encoding-for-qaoa", ["qaoa"], "avancado"),
    ("ghz-spacetime-codes", ["correcao_de_erros"], "avancado"),
]

IBM_LEARNING = [
    ("courses/basics-of-quantum-information/single-systems/introduction", ["fundamentos", "algebra_linear"], "basico"),
    ("courses/basics-of-quantum-information/single-systems/quantum-information", ["fundamentos", "estados"], "basico"),
    ("courses/basics-of-quantum-information/multiple-systems/introduction", ["fundamentos", "produto_tensorial"], "basico"),
    ("courses/basics-of-quantum-information/quantum-circuits/introduction", ["circuitos"], "basico"),
    ("courses/basics-of-quantum-information/entanglement-in-action/introduction", ["emaranhamento", "teletransporte"], "basico"),
    ("courses/fundamentals-of-quantum-algorithms/quantum-query-algorithms/introduction", ["algoritmos", "deutsch_jozsa"], "intermediario"),
    ("courses/fundamentals-of-quantum-algorithms/quantum-algorithmic-foundations/introduction", ["algoritmos", "complexidade"], "intermediario"),
    ("courses/fundamentals-of-quantum-algorithms/phase-estimation-and-factoring/introduction", ["estimativa_de_fase", "shor"], "avancado"),
    ("courses/fundamentals-of-quantum-algorithms/grover-algorithm/introduction", ["grover"], "intermediario"),
    ("courses/foundations-of-quantum-error-correction/correcting-quantum-errors/introduction", ["correcao_de_erros"], "avancado"),
    ("courses/foundations-of-quantum-error-correction/stabilizer-formalism/introduction", ["estabilizadores", "correcao_de_erros"], "avancado"),
    ("courses/foundations-of-quantum-error-correction/quantum-code-constructions/introduction", ["correcao_de_erros", "surface_code"], "avancado"),
    ("courses/general-formulation-of-quantum-information/density-matrices/introduction", ["estados", "matriz_densidade"], "avancado"),
    ("courses/general-formulation-of-quantum-information/quantum-channels/introduction", ["ruido", "canais"], "avancado"),
    ("courses/variational-algorithm-design/introduction", ["variacional", "vqe"], "avancado"),
    ("courses/quantum-computing-in-practice/introduction", ["pratica", "hardware"], "intermediario"),
    ("courses/utility-scale-quantum-computing/introduction", ["pratica", "hardware"], "avancado"),
    ("courses/quantum-diagonalization-algorithms/introduction", ["simulacao_hamiltoniana"], "avancado"),
    ("courses/quantum-machine-learning/introduction", ["aprendizado_de_maquina"], "avancado"),
]

AER = "https://qiskit.github.io/qiskit-aer"
AER_PAGES = [
    ("tutorials/1_aersimulator.html", ["simulacao", "aer"], "intermediario"),
    ("tutorials/2_device_noise_simulation.html", ["ruido", "simulacao"], "avancado"),
    ("tutorials/3_building_noise_models.html", ["ruido"], "avancado"),
    ("tutorials/4_custom_gate_noise.html", ["ruido"], "avancado"),
    ("tutorials/5_noise_transformation.html", ["ruido"], "avancado"),
    ("tutorials/6_extended_stabilizer_tutorial.html", ["estabilizadores", "simulacao"], "avancado"),
    ("tutorials/7_matrix_product_state_method.html", ["mps", "simulacao", "redes_tensoriais"], "avancado"),
    ("apidocs/aer_provider.html", ["simulacao", "aer"], "intermediario"),
    ("apidocs/aer_noise.html", ["ruido"], "avancado"),
    ("apidocs/aer_primitives.html", ["primitivas", "aer"], "intermediario"),
    ("apidocs/aer_quantum_info.html", ["quantum_info", "aer"], "avancado"),
    ("apidocs/aer_library.html", ["simulacao", "aer"], "avancado"),
    ("stubs/qiskit_aer.AerSimulator.html", ["simulacao", "aer", "mps", "estabilizadores"], "intermediario"),
    ("howtos/running_gpu.html", ["simulacao", "gpu"], "avancado"),
    ("howtos/threadpool.html", ["simulacao", "desempenho"], "avancado"),
]

COMMUNITY = [
    ("qiskit-optimization", "tutorials/01_quadratic_program.html", ["otimizacao_combinatoria"], "avancado"),
    ("qiskit-optimization", "tutorials/03_minimum_eigen_optimizer.html", ["otimizacao_combinatoria", "qaoa"], "avancado"),
    ("qiskit-optimization", "tutorials/06_examples_max_cut_and_tsp.html", ["otimizacao_combinatoria", "qaoa"], "avancado"),
    ("qiskit-optimization", "index.html", ["otimizacao_combinatoria"], "intermediario"),
    ("qiskit-machine-learning", "index.html", ["aprendizado_de_maquina"], "avancado"),
    ("qiskit-algorithms", "index.html", ["algoritmos", "vqe"], "avancado"),
    ("qiskit-algorithms", "tutorials/01_algorithms_introduction.html", ["algoritmos"], "intermediario"),
    ("qiskit-algorithms", "tutorials/02_vqe_advanced_options.html", ["vqe"], "avancado"),
    ("qiskit-algorithms", "tutorials/05_qaoa.html", ["qaoa"], "avancado"),
    ("qiskit-algorithms", "tutorials/06_grover.html", ["grover"], "intermediario"),
    ("qiskit-algorithms", "tutorials/09_IQPE.html", ["estimativa_de_fase"], "avancado"),
    ("qiskit-nature", "index.html", ["quimica_quantica"], "avancado"),
    ("qiskit-dynamics", "index.html", ["simulacao_hamiltoniana"], "avancado"),
]

OPENQASM = [
    ("intro.html", ["openqasm3"], "basico"),
    ("language/index.html", ["openqasm3"], "basico"),
    ("language/comments.html", ["openqasm3"], "basico"),
    ("language/types.html", ["openqasm3", "tipos"], "intermediario"),
    ("language/gates.html", ["openqasm3", "portas"], "intermediario"),
    ("language/insts.html", ["openqasm3", "medicao"], "intermediario"),
    ("language/classical.html", ["openqasm3", "controle_classico"], "avancado"),
    ("language/standard_library.html", ["openqasm3", "portas"], "intermediario"),
    ("language/directives.html", ["openqasm3"], "avancado"),
    ("language/scope.html", ["openqasm3"], "avancado"),
    ("language/subroutines.html", ["openqasm3"], "avancado"),
    ("language/delays.html", ["openqasm3", "temporizacao"], "avancado"),
    ("language/pulses.html", ["openqasm3", "pulsos"], "avancado"),
    ("language/openpulse.html", ["openqasm3", "pulsos"], "avancado"),
    ("versions/3.0/language/index.html", ["openqasm3"], "basico"),
    ("release_notes.html", ["openqasm3", "versoes"], "basico"),
]

PENNYLANE = [
    ("https://docs.pennylane.ai/en/stable/introduction/circuits.html", ["circuitos"], "basico"),
    ("https://docs.pennylane.ai/en/stable/introduction/operations.html", ["portas"], "basico"),
    ("https://docs.pennylane.ai/en/stable/introduction/measurements.html", ["medicao"], "basico"),
    ("https://docs.pennylane.ai/en/stable/introduction/templates.html", ["variacional", "ansatz"], "intermediario"),
    ("https://docs.pennylane.ai/en/stable/introduction/interfaces.html", ["diferenciacao"], "intermediario"),
    ("https://docs.pennylane.ai/en/stable/introduction/compiling_circuits.html", ["compilacao", "otimizacao"], "avancado"),
    ("https://docs.pennylane.ai/en/stable/introduction/dynamic_quantum_circuits.html", ["circuitos_dinamicos"], "avancado"),
    ("https://docs.pennylane.ai/en/stable/introduction/operations.html#state-preparation", ["estados"], "intermediario"),
    ("https://docs.pennylane.ai/en/stable/introduction/chemistry.html", ["quimica_quantica"], "avancado"),
    ("https://docs.pennylane.ai/en/stable/introduction/inspecting_circuits.html", ["depuracao"], "intermediario"),
    ("https://docs.pennylane.ai/en/stable/introduction/importing_workflows.html", ["interoperabilidade", "openqasm"], "intermediario"),
    ("https://pennylane.ai/qml/demos/tutorial_vqe", ["vqe"], "avancado"),
    ("https://pennylane.ai/qml/demos/tutorial_qaoa_intro", ["qaoa"], "avancado"),
    ("https://pennylane.ai/qml/demos/tutorial_qft", ["qft"], "intermediario"),
    ("https://pennylane.ai/qml/demos/tutorial_grovers_algorithm", ["grover"], "intermediario"),
    ("https://pennylane.ai/qml/demos/tutorial_teleportation", ["teletransporte"], "basico"),
    ("https://pennylane.ai/qml/demos/tutorial_qpe", ["estimativa_de_fase"], "avancado"),
    ("https://pennylane.ai/qml/demos/tutorial_barren_plateaus", ["variacional"], "avancado"),
    ("https://pennylane.ai/qml/demos/tutorial_noisy_circuits", ["ruido"], "avancado"),
    ("https://pennylane.ai/qml/demos/tutorial_error_mitigation", ["mitigacao"], "avancado"),
    ("https://pennylane.ai/qml/demos/tutorial_mbqc", ["fundamentos"], "avancado"),
    ("https://pennylane.ai/qml/demos/tutorial_unitary_designs", ["unitarias"], "avancado"),
    ("https://pennylane.ai/qml/demos/tutorial_zx_calculus", ["zx", "otimizacao"], "avancado"),
    ("https://pennylane.ai/qml/demos/tutorial_clifford_circuit_simulations", ["clifford", "simulacao"], "avancado"),
    ("https://pennylane.ai/qml/demos/tutorial_mps", ["mps", "redes_tensoriais"], "avancado"),
]

CIRQ = [
    ("start/basics", ["circuitos"], "basico"), ("build/circuits", ["circuitos"], "basico"),
    ("build/gates", ["portas"], "basico"), ("build/qubits", ["qubits"], "basico"),
    ("build/operators", ["operadores"], "intermediario"), ("build/custom_gates", ["portas"], "intermediario"),
    ("build/protocols", ["unitarias"], "avancado"), ("build/classical_control", ["controle_classico"], "avancado"),
    ("simulate/simulation", ["simulacao"], "intermediario"), ("simulate/noisy_simulation", ["ruido", "simulacao"], "avancado"),
    ("simulate/mps", ["mps", "simulacao"], "avancado"), ("simulate/stabilizer_simulation", ["estabilizadores"], "avancado"),
    ("transform/transformers", ["otimizacao", "transpilacao"], "avancado"),
    ("transform/routing_transformer", ["roteamento"], "avancado"),
    ("hardware/devices", ["hardware"], "intermediario"), ("build/interop", ["interoperabilidade"], "intermediario"),
]

MS = [
    ("concepts-the-qubit", ["fundamentos", "qubits"], "basico"),
    ("concepts-vectors-and-matrices", ["algebra_linear"], "basico"),
    ("concepts-dirac-notation", ["algebra_linear", "notacao_dirac"], "basico"),
    ("concepts-multiple-qubits", ["produto_tensorial", "emaranhamento"], "basico"),
    ("concepts-pauli-measurements", ["medicao", "pauli"], "intermediario"),
    ("concepts-circuits", ["circuitos"], "basico"),
    ("concepts-entanglement", ["emaranhamento"], "basico"),
    ("concepts-superposition", ["superposicao"], "basico"),
    ("concepts-oracles", ["oraculos", "algoritmos"], "intermediario"),
    ("concepts-grovers", ["grover"], "intermediario"),
    ("concepts-phase-estimation", ["estimativa_de_fase"], "avancado"),
    ("concepts-error-correction", ["correcao_de_erros"], "avancado"),
    ("concepts-overview", ["fundamentos"], "basico"),
    ("qsharp-overview", ["qsharp"], "basico"),
    ("qsharp-ways-to-work", ["qsharp"], "basico"),
    ("overview-resources-estimator", ["estimativa_de_recursos"], "avancado"),
]

BRAKET = [
    ("braket-constructing-circuit.html", ["circuitos"], "basico"),
    ("braket-devices.html", ["hardware", "simulacao"], "basico"),
    ("braket-send-to-local-simulator.html", ["simulacao"], "basico"),
    ("braket-submit-tasks.html", ["execucao"], "intermediario"),
    ("braket-openqasm.html", ["openqasm3", "interoperabilidade"], "intermediario"),
    ("braket-openqasm-supported-features.html", ["openqasm3"], "intermediario"),
    ("braket-result-types.html", ["resultados", "medicao"], "intermediario"),
    ("braket-error-mitigation.html", ["mitigacao"], "avancado"),
    ("braket-hybrid-jobs.html", ["variacional", "hibrido"], "avancado"),
    ("what-is-braket.html", ["introducao"], "basico"),
]

QUTIP = [
    ("https://qutip.readthedocs.io/en/latest/guide/guide-basics.html", ["algebra_linear", "estados"], "basico"),
    ("https://qutip.readthedocs.io/en/latest/guide/guide-states.html", ["estados", "matriz_densidade"], "intermediario"),
    ("https://qutip.readthedocs.io/en/latest/guide/guide-tensor.html", ["produto_tensorial"], "intermediario"),
    ("https://qutip.readthedocs.io/en/latest/guide/dynamics/dynamics-master.html", ["dinamica", "ruido"], "avancado"),
    ("https://qutip.readthedocs.io/en/latest/guide/guide-measurement.html", ["medicao"], "intermediario"),
    ("https://qutip-qip.readthedocs.io/en/stable/qip-basics.html", ["circuitos"], "intermediario"),
    ("https://qutip-qip.readthedocs.io/en/stable/qip-simulator.html", ["simulacao"], "intermediario"),
    ("https://qutip-qip.readthedocs.io/en/stable/qip-processor.html", ["hardware", "ruido"], "avancado"),
]

PYDOCS = [
    ("https://docs.python.org/3/library/ast.html", ["python", "analise_estatica"], "avancado"),
    ("https://docs.python.org/3/library/cmath.html", ["python", "numeros_complexos"], "basico"),
    ("https://docs.python.org/3/library/math.html", ["python"], "basico"),
    ("https://docs.python.org/3/library/exceptions.html", ["python", "depuracao", "erros"], "intermediario"),
    ("https://docs.python.org/3/tutorial/errors.html", ["python", "depuracao", "erros"], "basico"),
    ("https://docs.python.org/3/library/traceback.html", ["python", "depuracao"], "intermediario"),
    ("https://docs.python.org/3/tutorial/modules.html", ["python", "imports"], "basico"),
    ("https://docs.python.org/3/reference/import.html", ["python", "imports"], "avancado"),
    ("https://numpy.org/doc/stable/reference/routines.linalg.html", ["algebra_linear", "numpy"], "intermediario"),
    ("https://numpy.org/doc/stable/user/basics.broadcasting.html", ["numpy"], "intermediario"),
    ("https://numpy.org/doc/stable/reference/generated/numpy.kron.html", ["produto_tensorial", "numpy"], "basico"),
    ("https://numpy.org/doc/stable/reference/generated/numpy.allclose.html", ["numpy", "equivalencia"], "basico"),
    ("https://numpy.org/doc/stable/user/basics.types.html", ["numpy", "numeros_complexos"], "basico"),
    ("https://numpy.org/doc/stable/reference/generated/numpy.linalg.eigh.html", ["algebra_linear", "numpy"], "intermediario"),
    ("https://docs.scipy.org/doc/scipy/reference/linalg.html", ["algebra_linear", "scipy"], "intermediario"),
    ("https://docs.scipy.org/doc/scipy/reference/generated/scipy.linalg.expm.html", ["algebra_linear", "scipy", "unitarias"], "intermediario"),
    ("https://docs.scipy.org/doc/scipy/reference/optimize.html", ["otimizacao_classica", "variacional"], "intermediario"),
    ("https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.minimize.html", ["otimizacao_classica", "variacional"], "intermediario"),
    ("https://docs.scipy.org/doc/scipy/tutorial/optimize.html", ["otimizacao_classica"], "intermediario"),
]

ARXIV = [
    ("1707.03429", "OpenQASM 2", ["openqasm2"]), ("2104.14722", "OpenQASM 3", ["openqasm3"]),
    ("2405.08810", "Quantum computing with Qiskit", ["qiskit", "transpilacao"]),
    ("1809.02573", "SABRE qubit mapping", ["roteamento", "layout"]),
    ("1411.4028", "QAOA", ["qaoa"]), ("1304.3061", "VQE", ["vqe"]),
    ("1801.00862", "NISQ", ["fundamentos", "hardware"]),
    ("quant-ph/9508027", "Shor factoring", ["shor", "algoritmos"]),
    ("quant-ph/9605043", "Grover search", ["grover", "algoritmos"]),
    ("1208.0928", "Surface codes", ["correcao_de_erros", "surface_code"]),
    ("quant-ph/9705052", "Stabilizer codes (Gottesman)", ["estabilizadores", "correcao_de_erros"]),
    ("quant-ph/0406196", "Improved simulation of stabilizer circuits", ["estabilizadores", "simulacao"]),
    ("quant-ph/0301063", "Slightly entangled simulation (MPS)", ["mps", "simulacao"]),
    ("quant-ph/9503016", "Elementary gates", ["decomposicao", "portas"]),
    ("quant-ph/0406176", "Synthesis of quantum logic circuits", ["sintese"]),
    ("quant-ph/0308006", "Optimal two-qubit circuits", ["sintese", "decomposicao"]),
    ("1206.0758", "Meet-in-the-middle synthesis", ["sintese", "otimizacao"]),
    ("1710.07345", "Automated optimization of large circuits", ["otimizacao"]),
    ("1902.03178", "ZX-calculus circuit simplification", ["zx", "otimizacao"]),
    ("1612.02058", "Error mitigation for short-depth circuits", ["mitigacao"]),
    ("2210.00921", "Quantum error mitigation review", ["mitigacao"]),
    ("1704.05018", "Hardware-efficient VQE", ["vqe", "ansatz"]),
    ("1803.11173", "Barren plateaus", ["variacional"]),
    ("2012.09265", "Variational quantum algorithms review", ["variacional"]),
    ("2101.08448", "NISQ algorithms review", ["algoritmos", "variacional"]),
    ("quant-ph/9511026", "Quantum measurements and the Abelian stabilizer problem (Kitaev)", ["estimativa_de_fase"]),
    ("quant-ph/0410184", "Solovay-Kitaev algorithm", ["sintese", "decomposicao"]),
]


# IDs já indexados em instalações existentes: mantidos para não duplicar a coleção.
LEGADO = {
    f"{IBM}/guides/circuit-library": "ibm_circuit_library",
    f"{IBM}/guides/construct-circuits": "ibm_construct_circuits",
    f"{IBM}/guides/visualize-circuits": "ibm_visualize_circuits",
    f"{IBM}/guides/bit-ordering": "ibm_bit_ordering",
    f"{IBM}/guides/measure-qubits": "ibm_measure_qubits",
    f"{IBM}/guides/simulate-with-qiskit-sdk-primitives": "ibm_simulate_with_qiskit_sdk_primitives",
    f"{IBM}/guides/transpile": "ibm_intro_transpilacao",
    f"{IBM}/guides/transpiler-stages": "ibm_etapas_transpilacao",
    f"{IBM}/guides/set-optimization": "ibm_niveis_otimizacao",
    f"{IBM}/guides/defaults-and-configuration-options": "ibm_transpilation_defaults",
    f"{IBM}/guides/classical-feedforward-and-control-flow": "ibm_classical_feedforward_and_control_flow",
    f"{IBM}/api/qiskit/2.6/circuit": "ibm_api_circuit",
    f"{IBM}/api/qiskit/2.6/qiskit.circuit.QuantumCircuit": "ibm_api_qiskit_circuit_QuantumCircuit",
    f"{IBM}/api/qiskit/2.6/qiskit.circuit.Parameter": "ibm_api_qiskit_circuit_Parameter",
    f"{IBM}/api/qiskit/2.6/circuit_library": "ibm_api_circuit_library",
    f"{IBM}/api/qiskit/2.6/quantum_info": "ibm_api_quantum_info",
    f"{IBM}/api/qiskit/2.6/qiskit.quantum_info.Statevector": "ibm_api_qiskit_quantum_info_Statevector",
    f"{IBM}/api/qiskit/2.6/qiskit.quantum_info.Operator": "ibm_api_qiskit_quantum_info_Operator",
    f"{IBM}/api/qiskit/2.6/primitives": "ibm_api_primitives",
    f"{IBM}/api/qiskit/2.6/qasm2": "ibm_api_qasm2",
    f"{IBM}/api/qiskit/2.6/qasm3": "ibm_api_qasm3",
}


def _id(prefixo, texto):
    return (prefixo + "_" + re.sub(r"[^a-z0-9]+", "_", texto.lower())).strip("_")[:90]


def candidatos():
    fontes = []

    def add(document_id, url, biblioteca, temas, nivel, tipo, licenca, versao):
        document_id = LEGADO.get(url, document_id)
        fontes.append({"document_id": document_id, "url": url, "versao": versao, "biblioteca": biblioteca,
                       "temas": temas, "nivel": nivel, "tipo_fonte": tipo, "licenca": licenca, "idioma": "en"})

    for slug, temas, nivel in IBM_GUIDES:
        add(_id("ibm_guia", slug), f"{IBM}/guides/{slug}", "qiskit", temas, nivel, "documentacao_oficial", IBM_LIC,
            "guia online; conferir requisitos na página")
    for nome, temas, nivel in IBM_API:
        add(_id("ibm_api", nome), f"{IBM}/api/qiskit/2.6/{nome}", "qiskit", temas, nivel, "documentacao_oficial", IBM_LIC,
            "API Qiskit SDK 2.6")
    for slug, temas, nivel in IBM_MIGRATION:
        add(_id("ibm_migracao", slug), f"{IBM}/migration-guides/{slug}", "qiskit", temas, nivel,
            "documentacao_oficial", IBM_LIC, "guia de migração")
    for slug, temas, nivel in IBM_TUTORIALS:
        add(_id("ibm_tutorial", slug), f"{IBM}/tutorials/{slug}", "qiskit", temas, nivel, "tutorial_oficial", IBM_LIC,
            "tutorial online")
    for slug, temas, nivel in IBM_LEARNING:
        add(_id("ibm_curso", slug.replace("courses/", "")), f"https://quantum.cloud.ibm.com/learning/en/{slug}", "qiskit",
            temas, nivel, "material_educacional", IBM_LIC, "curso online IBM Quantum Learning")
    for pagina, temas, nivel in AER_PAGES:
        add(_id("aer", pagina.replace(".html", "")), f"{AER}/{pagina}", "qiskit_aer", temas, nivel,
            "documentacao_oficial", "Apache-2.0 (repositório Qiskit/qiskit-aer)", "documentação estável do qiskit-aer")
    for projeto, pagina, temas, nivel in COMMUNITY:
        add(_id(projeto.replace("-", "_"), pagina.replace(".html", "")),
            f"https://qiskit-community.github.io/{projeto}/{pagina}", "qiskit", temas, nivel,
            "documentacao_comunitaria", "Apache-2.0 (projeto qiskit-community)", "documentação do projeto comunitário")
    for pagina, temas, nivel in OPENQASM:
        add(_id("openqasm", pagina.replace(".html", "")), f"https://openqasm.com/{pagina}", "openqasm", temas, nivel,
            "especificacao_oficial", "Apache-2.0 (openqasm/openqasm)", "especificação OpenQASM 3")
    for url, temas, nivel in PENNYLANE:
        if "#" in url:
            continue  # fragmentos não são aceitos pelo coletor
        add(_id("pennylane", url.split("/")[-1].replace(".html", "")), url, "pennylane", temas, nivel,
            "documentacao_oficial", "Apache-2.0 (documentação) / termos do site pennylane.ai", "versão estável")
    for slug, temas, nivel in CIRQ:
        add(_id("cirq", slug), f"https://quantumai.google/cirq/{slug}", "cirq", temas, nivel,
            "documentacao_oficial", "Apache-2.0 (quantumlib/Cirq)", "documentação Cirq")
    for slug, temas, nivel in MS:
        add(_id("msq", slug), f"https://learn.microsoft.com/en-us/azure/quantum/{slug}", "qsharp", temas, nivel,
            "documentacao_oficial", "CC-BY-4.0 (Microsoft Learn), consultar termos", "Azure Quantum docs")
    for pagina, temas, nivel in BRAKET:
        add(_id("braket", pagina.replace(".html", "")), f"https://docs.aws.amazon.com/braket/latest/developerguide/{pagina}",
            "braket", temas, nivel, "documentacao_oficial", "CC-BY-SA-4.0 (AWS docs), consultar termos", "Developer Guide")
    for url, temas, nivel in QUTIP:
        add(_id("qutip", url.split("/")[-1].replace(".html", "")), url, "qutip", temas, nivel,
            "documentacao_oficial", "BSD-3-Clause (QuTiP)", "documentação QuTiP")
    for url, temas, nivel in PYDOCS:
        biblioteca = "python" if "python.org" in url else ("numpy" if "numpy.org" in url else "scipy")
        add(_id(biblioteca, "_".join(url.rstrip("/").split("/")[-2:]).replace(".html", "")), url, biblioteca, temas, nivel,
            "documentacao_oficial", "PSF/BSD, consultar termos", "versão estável")
    add("algorithm_zoo", "https://quantumalgorithmzoo.org/", "geral", ["algoritmos", "complexidade"], "avancado",
        "catalogo_academico", "consultar termos do site", "catálogo mantido por Stephen Jordan")
    for codigo, titulo, temas in ARXIV:
        add(_id("arxiv", codigo), f"https://arxiv.org/abs/{codigo}", "geral", temas, "avancado",
            "artigo_academico", "metadados e resumo do arXiv; texto completo sob a licença do autor", titulo)
    return fontes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    grupo = parser.add_mutually_exclusive_group(required=True)
    grupo.add_argument("--candidatos", action="store_true")
    grupo.add_argument("--aplicar", type=Path, help="relatório de --verificar-catalogo")
    args = parser.parse_args()
    fontes = candidatos()
    if args.aplicar:
        relatorio = json.loads(args.aplicar.read_text(encoding="utf-8"))
        aprovadas = {f["document_id"] for f in relatorio["fontes"] if f["status"] == "ok"}
        fontes = [f for f in fontes if f["document_id"] in aprovadas]
        print(f"{len(fontes)} fontes aprovadas de {len(relatorio['fontes'])} verificadas.")
    else:
        print(f"{len(fontes)} fontes candidatas.")
    CATALOGO.write_bytes((json.dumps(fontes, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
