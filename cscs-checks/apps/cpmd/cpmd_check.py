# Copyright 2016-2021 Swiss National Supercomputing Centre (CSCS/ETH Zurich)
# ReFrame Project Developers. See the top-level LICENSE file for details.
#
# SPDX-License-Identifier: BSD-3-Clause

import reframe as rfm
import reframe.utility.sanity as sn


@rfm.simple_test
class CPMDCheck(rfm.RunOnlyRegressionTest):
    scale = parameter(['small', 'large'])
    descr = 'CPMD check (C4H6 metadynamics)'
    maintainers = ['AJ', 'LM']
    tags = {'production'}
    valid_systems = ['daint:gpu']
    num_tasks_per_node = 1
    valid_prog_environs = ['builtin']
    modules = ['CPMD']
    executable = 'cpmd.x'
    executable_opts = ['ana_c4h6.in > stdout.txt']
    readonly_files = ['ana_c4h6.in', 'C_MT_BLYP', 'H_MT_BLYP']
    use_multithreading = True
    strict_check = False
    extra_resources = {
        'switches': {
            'num_switches': 1
        }
    }

    @run_before('run')
    def set_something(self):
        if self.scale == 'small':
            self.num_tasks = 9
            self.valid_systems += ['dom:gpu']
        else:
            self.num_tasks = 16

        if self.scale == 'small':
            self.reference = {
                'daint:gpu': {
                    'time': (285.5, None, 0.20, 's')
                },
                'dom:gpu': {
                    'time': (332.0, None, 0.15, 's')
                }
            }
        else:
            self.reference = {
                'daint:gpu': {
                    'time': (245.0, None, 0.59, 's')
                }
            }

    @sanity_function
    def assert_energy_diff(self):
        #  OpenMP version of CPMD segfaults
        #  self.variables = { 'OMP_NUM_THREADS' : '8' }
        energy = sn.extractsingle(
            r'CLASSICAL ENERGY\s+-(?P<result>\S+)',
            'stdout.txt', 'result', float)
        energy_reference = 25.81
        energy_diff = sn.abs(energy - energy_reference)
        return sn.assert_lt(energy_diff, 0.26)

    @performance_function('s')
    def time(self):
        return sn.extractsingle(r'^ cpmd(\s+[\d\.]+){3}\s+(?P<perf>\S+)',
                                'stdout.txt', 'perf', float)
