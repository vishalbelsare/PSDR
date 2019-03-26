# Subspace based dimension reduction techniques
from __future__ import division, print_function
import numpy as np
import scipy.linalg
from scipy.spatial.distance import cdist
import matplotlib.pyplot as plt
import cvxpy as cp
import cvxopt
from itertools import combinations

from subspace import SubspaceBasedDimensionReduction
from geometry import candidate_furthest_points
from minimax import minimax
from function import BaseFunction

__all__ = ['LipschitzMatrix', 'LipschitzConstant']

class LipschitzMatrix(SubspaceBasedDimensionReduction):
	r"""Constructs the subspace-based dimension reduction from the Lipschitz Matrix.

	The Lipschitz matrix :math:`\mathbf{L} \in \mathbb{R}^{m \times m}` a matrix that 
	acts analogously to the Lipschitz constant, defining a function class where

	.. math::

		\lbrace f: \mathbb{R}^m\to \mathbb{R}: |f(\mathbf{x}_1) - f(\mathbf{x}_2)| \le \|\mathbf{L}(\mathbf{x}_1 - \mathbf{x}_2\|_2 \rbrace.

	In general we cannot determine the Lipschitz matrix analytically. 
	Instead we seek to estimate it via the lower bound based on samples :math:`\lbrace \mathbf{x}_i, f(\mathbf{x}_i)\rbrace_i`
	and/or gradients :math:`\lbrace \nabla f(\mathbf{x}_i)\rbrace_i`.
	Here we do so by solving a semidefinite program for the symmetric positive definite matrix :math:`\mathbf{M} \in \mathbb{R}^{m\times m}`:

	.. math::

		\min_{\mathbf{H} \in \mathbb{S}^{m}_+} & \ \text{Trace } \mathbf{H} \\
		\text{such that} & \ |f(\mathbf{x}_i) - f(\mathbf{x}_j)|^2 \le (\mathbf{x}_i - \mathbf{x}_j)^\top \mathbf{H} (\mathbf{x}_i - \mathbf{x}_j) \\
		& \ \nabla f(\mathbf{x}_k) \nabla f(\mathbf{x}_k)^\top \preceq \mathbf{H}

	Parameters
	----------
	**kwargs: dict (optional)
		Additional parameters to pass to cvxpy
	"""
	def __init__(self, epsilon = None, method = 'cvxopt', **kwargs):
		self._U = None
		self._L = None
		self.kwargs = kwargs

		if 'solver' not in self.kwargs:
			self.kwargs['solver'] = cp.CVXOPT
		
		if method == 'cvxopt':
			self._build_lipschitz_matrix = self._build_lipschitz_matrix_cvxopt
		elif method == 'param':	
			self._build_lipschitz_matrix = self._build_lipschitz_matrix_param
		elif method == 'cvxpy':	
			self._build_lipschitz_matrix = self._build_lipschitz_matrix_cvxpy
		else:
			raise NotImplementedError

		if epsilon is not None:
			assert epsilon >= 0, "Epsilon must be positive"
			epsilon = float(epsilon)
		self.epsilon = epsilon
		
		if 'abstol' not in kwargs:
			self.kwargs['abstol'] = 1e-7
		if 'reltol' not in kwargs:
			self.kwargs['reltol'] = 1e-6
		if 'feastol' not in kwargs:
			self.kwargs['feastol'] = 1e-7
		if 'refinement' not in kwargs:
			self.kwargs['refinement'] = 1

	def fit(self, X = None, fX = None, grads = None):
		r""" Find the Lipschitz matrix


		Parameters
		----------
		X : array-like (N, m), optional
			Input coordinates for function samples 
		fX: array-like (N,), optional
			Values of the function at X[i]
		grads: array-like (N,m), optional
			Gradients of the function evaluated anywhere	
		"""
		self._init_dim(X = X, grads = grads)

		if X is not None and fX is not None:
			N = len(X)
			assert len(fX) == N, "Dimension of input and output does not match"
			X = np.atleast_2d(np.array(X))
			self._dimension = X.shape[1]
			fX = np.array(fX).reshape(X.shape[0])

		elif X is None and fX is None:
			X = np.zeros((0,len(self)))
			fX = np.zeros((0,))
		else:
			raise AssertionError("X and fX must both be specified simultaneously or not specified")

		if grads is not None:
			grads = np.array(grads).reshape(-1,len(self))
		else:
			grads = np.zeros((0, len(self)))
	
		# Scale the output units for numerical stability	
		try:	
			scale1 = np.max(fX) - np.min(fX)
		except ValueError:
			scale1 = 1.
		try:
			scale2 = np.max([np.linalg.norm(grad) for grad in grads])
		except ValueError:
			scale2 = 1.
		scale = max(scale1, scale2)
	
		if self.epsilon is not None:
			epsilon = self.epsilon/scale
		else:
			epsilon = 0.
		H = self._build_lipschitz_matrix(X, fX/scale, grads/scale, epsilon)

		# Compute the important directions
		#self._U, _, _ = np.linalg.svd(self._H)
		ew, U = scipy.linalg.eigh(H)

		# Because eigenvalues are in ascending order, the subspace basis needs to be flipped
		# Fix the signs for the subspace directions 
		self._fix_subspace_signs(U[:,::-1], X, fX/scale, grads/scale)
		

		# Force to be SPD
		self._H = scale**2 * U.dot(np.diag(np.maximum(ew,0)).dot(U.T))

		# Compute the Lipschitz matrix 
		#self._L = scipy.linalg.cholesky(self.H[::-1][:,::-1], lower = False)[::-1][:,::-1]
		self._L = scale * U.dot(np.diag(np.sqrt(np.maximum(ew, 0))).dot(U.T))

	@property
	def X(self): return self._X
	
	@property
	def fX(self): return self._fX

	@property
	def grads(self): return self._grads

	@property
	def U(self): return np.copy(self._U)

	@property
	def H(self): 
		r""" The symmetric positive definite solution to the semidefinite program
		"""
		return self._H

	@property
	def L(self): 
		r""" The Lipschitz matrix estimate based on samples
		"""
		return self._L

	def _build_lipschitz_matrix_cvxpy(self, X, fX, grads, epsilon):
		# Constrain H to symmetric positive semidefinite (PSD)
		H = cp.Variable( (len(self), len(self)), PSD = True)
		
		constraints = []		

		# Sample constraint	
		for i in range(len(X)):
			for j in range(i+1, len(X)):
				lhs = (np.abs(fX[i] - fX[j]) - epsilon)**2
				y = X[i] - X[j]
				# y.T M y
				#rhs = H.__matmul__(y).__rmatmul__(y.T)
				rhs = cp.quad_form(y, H)
				constraints.append(lhs <= rhs)
			
		# gradient constraints
		for g in grads:
			constraints.append( np.outer(g,g) << H)

		problem = cp.Problem(cp.Minimize(cp.trace(H)), constraints)
		problem.solve(**self.kwargs)
		
		return np.array(H.value).reshape(len(self),len(self))
				
	
	def _build_lipschitz_matrix_param(self, X, fX, grads, epsilon):
		r""" Use an explicit parameterization
		"""

		# Build the basis
		Es = []
		I = np.eye(len(self))
		for i in range(len(self)):
			ei = I[:,i]
			Es.append(np.outer(ei,ei))
			for j in range(i+1,len(self)):
				ej = I[:,j]
				Es.append(0.5*np.outer(ei+ej,ei+ej))

		alpha = cp.Variable(len(Es))
		H = cp.sum([alpha_i*Ei for alpha_i, Ei in zip(alpha, Es)])
		constraints = [H >> 0]
		
		# Construct gradient constraints
		for grad in grads:
			constraints.append( H >> np.outer(grad, grad))
		
		# Construct linear inequality constraints for samples
		A = np.zeros( (len(X)*(len(X)-1)//2, len(Es)) )
		b = np.zeros(A.shape[0])
		row = 0
		for i in range(len(X)):
			for j in range(i+1,len(X)):
				p = X[i] - X[j]
				A[row, :] = [p.dot(E.dot(p)) for E in Es]
				b[row] = (np.abs(fX[i] - fX[j]) - epsilon)**2
				row += 1

		if A.shape[0] > 0:	
			constraints.append( b <= alpha.__rmatmul__(A) )
		
		problem = cp.Problem(cp.Minimize(cp.sum(alpha)), constraints)
		problem.solve(**self.kwargs)

		alpha = np.array(alpha.value)
		H = np.sum([ alpha_i * Ei for alpha_i, Ei in zip(alpha, Es)], axis = 0)
		return H

	def _build_lipschitz_matrix_cvxopt(self, X, fX, grads, epsilon):
		r""" Directly accessing cvxopt rather than going through CVXPY results in noticable speed improvements
		"""	
		# Build the basis
		Es = []
		I = np.eye(len(self))
		for i in range(len(self)):
			ei = I[:,i]
			Es.append(np.outer(ei,ei))
			for j in range(i+1,len(self)):
				ej = I[:,j]
				Es.append(0.5*np.outer(ei+ej,ei+ej))

		Eten = np.array(Es)

		# Constraint matrices for CVXOPT
		# The format is 
		# sum_i x_i * G[i].reshape(square matrix) <= h.reshape(square matrix)
		Gs = []
		hs = []

		# Construct linear inequality constraints for samples
		for i in range(len(X)):
			for j in range(i+1,len(X)):
				p = X[i] - X[j]
				# Normalizing here seems to reduce the normalization once inside CVXOPT
				p_norm = np.linalg.norm(p)	
				# Vectorize to improve performance
				#G = [-p.dot(E.dot(p)) for E in Es]
				G = -np.tensordot(np.tensordot(Eten, p/p_norm, axes = (2,0)), p/p_norm, axes = (1,0))

				Gs.append(cvxopt.matrix(G).T)
				hs.append(cvxopt.matrix( [[ -(np.abs(fX[i] - fX[j]) - epsilon)**2/p_norm**2]]))

		# Add constraint to enforce H is positive-semidefinite
		# Flatten in Fortran---column major order
		G = cvxopt.matrix(np.vstack([E.flatten('F') for E in Es]).T)
		Gs.append(-G)
		hs.append(cvxopt.matrix(np.zeros((len(self),len(self)))))
	
		# Build constraints 	
		for grad in grads:
			Gs.append(-G)
			gg = -np.outer(grad, grad)
			hs.append(cvxopt.matrix(gg))

		# Setup objective	
		c = cvxopt.matrix(np.array([ np.trace(E) for E in Es]))
		
		if 'verbose' in self.kwargs:
			cvxopt.solvers.options['show_progress'] = self.kwargs['verbose']
		else:
			cvxopt.solvers.options['show_progress'] = False

		for name in ['abstol', 'reltol', 'feastol', 'refinement']:
			if name in self.kwargs:
				cvxopt.solvers.options[name] = self.kwargs[name]

		sol = cvxopt.solvers.sdp(c, Gs = Gs, hs = hs)
		alpha = sol['x']
		H = np.sum([ alpha_i * Ei for alpha_i, Ei in zip(alpha, Es)], axis = 0)
		return H

	def bounds(self, X, fX, Xtest):
		r""" Compute range of possible values at test points

		Parameters
		----------
		X: array-like (M, m)
			Array of input coordinates
		fX: array-like (M,)
			Array of function values
		Xtest: array-like (N, m)
			Array of places at which to determine uncertainty

		Returns
		-------
		lb: array-like (N,)
			Lower bounds
		ub: array-like (N,)
			Upper bounds
		"""
		lb = -np.inf*np.ones(Xtest.shape[0])
		ub = np.inf*np.ones(Xtest.shape[0])

		LX = self.L.dot(X.T).T
		LXtest = self.L.dot(Xtest.T).T
	
		# TODO: Replace with proper chunking to help vectorize this operation	
		for Lx, fx in zip(LX, fX):
			dist = cdist(Lx.reshape(1,-1), LXtest).flatten()
			lb = np.maximum(lb, fx - dist)
			ub = np.minimum(ub, fx + dist)
		
		return lb, ub
	
	def bounds_domain(self, X, fX, dom, verbose = False, **kwargs):
		r""" Compute the uncertainty for any point inside a domain

		Parameters
		----------
		X: array-like (M, m)
			Array of input coordinates
		fX: array-like (M,)
			Array of function values
		dom: Domain
			Domain on which to find exterma of limits

		Returns
		-------
		lb: float
			Lower bound
		ub: float
			Upper bound
		"""
		lower_bound = LowerBound(self.L, X, fX)
		upper_bound = UpperBound(self.L, X, fX)
		
		X0 = candidate_furthest_points(X, dom, L = self.L)

		# Iterate through all candidates
		lbs = []
		ubs = []
		for x0 in X0:
			# Lower bound
			x = minimax(lower_bound, x0, dom, verbose = verbose, trust_region = False)
			lbs.append(np.max(lower_bound(x)))
		
			# Upper bound
			x = minimax(upper_bound, x0, dom, verbose = verbose, trust_region = False)
			ubs.append(np.min(-upper_bound(x)))

		return float(np.min(lbs)), float(np.max(ubs))


class LipschitzConstant(LipschitzMatrix):
	r""" Computes the scalar Lipschitz constant
	"""

	def fit(self, X = None, fX = None, grads = None):
		r""" Compute the scalar Lipschitz constant


		Parameters
		----------
		X : array-like (N, m), optional
			Input coordinates for function samples 
		fX: array-like (N,), optional
			Values of the function at X[i]
		grads: array-like (N,m), optional
			Gradients of the function evaluated anywhere	
		"""
		self._init_dim(X = X, grads = grads)
		if self.epsilon is None: 
			assert grads is None, "Cannot compute epsilon-Lipschitz constant using gradient information"

		L = 0. 
		if self.epsilon is None:
			epsilon = 0
		else:
			epsilon = self.epsilon

		if X is not None:
			L_samp = np.max([ (abs(fX[i] - fX[j]) - epsilon )/ np.linalg.norm(X[i] - X[j]) 
				for i,j in combinations(range(M), 2)])
			L = max(L, L_samp)

		if grads is not None:	
			L_grad = np.max([np.linalg.norm(grad) for grad in grads])
			L = max(L, L_grad)

		self._L = L

	@property
	def L(self):
		return self._L*np.eye(len(self))

# Helper functions for determining bounds

class LowerBound(BaseFunction):
	def __init__(self, L, X, fX):
		self.L = L
		self.Y = np.dot(self.L, X.T).T
		self.fX = fX

	def eval(self, x):
		y = np.dot(self.L, x)
		norms = cdist(y.reshape(1,-1), self.Y, 'euclidean').flatten()
		return self.fX - norms

	def grad(self, x):
		y = np.dot(self.L, x)
		norms = cdist(y.reshape(1,-1), self.Y, 'euclidean').flatten()
		G = np.zeros([self.Y.shape[0], x.shape[0]] )
		I = (norms > 0)
		G[I,:] = -((y - self.Y[I]).T/norms[I]).T
		return G

class UpperBound(BaseFunction):
	def __init__(self, L, X, fX):
		self.L = L
		self.Y = np.dot(self.L, X.T).T
		self.fX = fX

	def eval(self, x):
		y = np.dot(self.L, x)
		norms = cdist(y.reshape(1,-1), self.Y, 'euclidean').flatten()
		return -(self.fX + norms)

	def grad(self, x):
		y = np.dot(self.L, x)
		norms = cdist(y.reshape(1,-1), self.Y, 'euclidean').flatten()
		G = np.zeros([self.Y.shape[0], x.shape[0]] )
		I = (norms > 0)
		G[I,:] = -((y - self.Y[I]).T/norms[I]).T
		return G



if __name__ == '__main__':
	from domains import BoxDomain
	X = np.random.randn(10,6)
	a = np.random.randn(6,)
	b = np.ones(6,)
	fX = np.dot(X, a).flatten()
	grads = np.tile(a, (X.shape[0], 1))
	lip = LipschitzMatrix()
	lip.fit(grads = grads)

	dom = BoxDomain(-np.ones(6), np.ones(6))
	dom2 = dom.add_constraints(b.reshape(1,-1), [0])
	print(dom2)
	lb, ub = lip.bounds_domain(X, fX, dom2, verbose = True)
	print(lb, ub)
