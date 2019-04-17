from __future__ import print_function
import numpy as np

from psdr import PolynomialApproximation, PolynomialFunction, LegendreTensorBasis, BoxDomain
from .checkder import *

def test_poly_der(dimension = 3, degree = 5):
	coef = np.random.randn(len(LegendreTensorBasis(dimension, degree)))
	pf = PolynomialFunction(dimension, degree, coef)

	x = np.random.randn(dimension)
	print(x)
	print(pf.eval(x))
	print(pf.grad(x))

	assert check_derivative(x, pf.eval, lambda x: pf.grad(x).flatten() ) < 1e-7

def test_poly_hess(dimension = 3, degree = 5):
	coef = np.random.randn(len(LegendreTensorBasis(dimension, degree)))
	pf = PolynomialFunction(dimension, degree, coef)
	
	x = np.random.randn(dimension)
	print(x)
	print(pf.eval(x))
	print(pf.hessian(x))

	assert check_hessian(x, pf.eval, lambda x: pf.hessian(x).reshape(dimension, dimension) ) < 1e-5


def test_poly_basis(dimension = 2, degree = 5):
	""" test different bases"""	
	coef = np.random.randn(len(LegendreTensorBasis(dimension, degree)))
	pf = PolynomialFunction(dimension, degree, coef)

	dom = BoxDomain(-np.ones(dimension), np.ones(dimension))
	X = dom.sample(100)
	fX = pf(X)
	Xtest = dom.sample(1000)
	fXtest = pf(Xtest)
	
	for basis in ['legendre', 'monomial', 'chebyshev', 'laguerre', 'hermite']:
		pa = PolynomialApproximation(degree, basis = basis)
		pa.fit(X, fX)
		assert np.linalg.norm(pa(Xtest) - fXtest, np.inf) < 1e-7


def test_poly_fit(dimension = 2, degree = 5):
	dom = BoxDomain(-np.ones(dimension), np.ones(dimension))
	X = dom.sample(100)

	fXnoise = np.random.randn(X.shape[0])

	for bound in ['lower', 'upper']:
		for norm in [1,2, np.inf]:
			for basis in ['legendre', 'monomial', 'chebyshev', 'laguerre', 'hermite']:
				pa = PolynomialApproximation(degree, basis = basis, norm = norm, bound = bound)
				pa.fit(X, fXnoise)
				if bound == 'lower':
					for y, fx in zip(pa(X), fXnoise):
						print('lower', y, fx)
						if y >= fx + 1e-7:
							assert False
					#assert np.all(pa(X) <= fXnoise +1e-7)
				if bound == 'upper':
					for y, fx in zip(pa(X), fXnoise):
						print('upper', y, fx)
						if y <= fx - 1e-7:
							assert False
					#assert np.all(pa(X) >= fXnoise -1e-7)
		
