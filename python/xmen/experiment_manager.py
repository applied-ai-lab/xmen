#!/usr/bin/env python3
"""A module holding the ExperimentManager implementation. The module can also be run activating the
experiment managers command line interface"""

#  Copyright (C) 2019  Robert J Weston, Oxford Robotics Institute
#
#  xmen
#  email:   robw@robots.ox.ac.uk
#  github: https://github.com/robw4/xmen/
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#  You should have received a copy of the GNU General Public License
#   along with this program. If not, see <http://www.gnu.org/licenses/>.

import sys
from shutil import copyfile, rmtree
import datetime
import ruamel.yaml
import pandas as pd
import subprocess
import glob
import time
import importlib.util
import copy
from xmen.utils import *
pd.set_option('expand_frame_repr', False)

from xmen.experiment import Experiment


class ExperimentManager(object):
    """A helper class with wrapped command line interface used to manage a set of experiments.

    The ExperimentManager represents a generic abstraction of a set of experiments. The set of experiments are
    configured from a set of common parameters and a run script. During execution the run script is passed the
    parameters of each experiment allowing the behaviour of each experiment to be specialized. Whilst, designed
    with managing deep learning experiments deployed on remote servers in mind, any set of
    experiments that can be defined in this way is compatible with the Experiment Manager:

    Command Line Interface:

        * ``init`` - Initialise an experiment from a set of hyper parameters that can be used to specialise a run script.sh::

            exp init 'path/to/defaults.yml' 'path/to/script.sh'
            exp init
            #(see method initialise)

        * ``register`` - Register a set of experiments by overloading the default parameters::

            exp register '{a: 1., b: 2.}'
            exp register '{a: 1 | 2, b: 2., c: a| b| c, d: , e: [a, b, c], f: {a: 1., b: 2., e:5}}'
            (see method register)

        * ``list`` - List all experiments::

            exp list
            # (see method list)

          An example output will look something like::

                 overrides       purpose                  created      status messages                                    commit
            0     a:3__b:4  initial test  07:17PM September 13, 2019  created       {}  dda2f262db819e14900c78d807b2182bf6111aef
            1     a:3__b:5  initial test  07:17PM September 13, 2019  created       {}  dda2f262db819e14900c78d807b2182bf6111aef
            2     a:4__b:4  initial test  07:17PM September 13, 2019  created       {}  dda2f262db819e14900c78d807b2182bf6111aef
            3     a:4__b:5  initial test  07:17PM September 13, 2019  created       {}  dda2f262db819e14900c78d807b2182bf6111aef

            Defaults:
            - created 06:34PM September 13, 2019
            - from  NewNewExperiment at /Users/robweston/projects/rad2sim2rad/python/rad2sim2rad/prototypes/test_typed_experiment.py
            - git local /Users/robweston/projects/rad2sim2rad
            - commit 51ad1eae73a2082e7636056fcd06e112d3fbca9c
            - remote ssh://git@mrgbuild.robots.ox.ac.uk:7999/~robw/rad2sim2rad.git

        * ``unlink`` - Unlink all experiments from the experiment manager matching name string::

            exp unlink 'a:1__b:2'
            exp unlink '*'
            exp unlink 'a:1__*'
            #(see method unlink)

        * ``rm`` - Remove folders of experiments that are no longer managed by the experiment manager::

            exp rm
            #(see method rm)

        * ``run`` - Run a group of experiments matching pattern::

            exp run '*' 'sh'                                        # Run all
            exp run 'a:1__b:2' '[sbatch', --gres, gpu:1]'           # yaml list of strings
            #(see method run)

    More Info:
        At its core the experiment manager maintains a single `root` directory::

            root
            ├── defaults.yml
            ├── experiment.yml
            ├── script.sh
            ├── {param}:{value}__{param}:{value}
            │   └── params.yml
            ├── {param}:{value}__{param}:{value}
            │   └── params.yml
            ...

        In the above we have:

            * ``defaults.yml`` defines a set of parameter keys and there default values shared by each experiment. It
               has the form::

                  # Optional additional Meta parameters
                  git:
                     local_path: /path/to/git/repo/defaults/were/defined/in
                     remote_url: /remote/repo/url
                     hash: 80dcfd98e6c3c17e1bafa72ee56744d4a6e30e80    # The git commit defaults were generatd at
                  created: 06:58PM September 16, 2019    # The date the defaults were created
                  definition:
                      path: /path/to/module/that/generated/defaults
                      class: TheNameOfTheClassThatProducedDefaults

                  # Default parameters
                  a: 3 #  This is the first parameter (default=3)
                  b: '4' #  This is the second parameter (default='4')

            * ``script.sh`` is a bash script. When run it takes a single argument ``'params.yml'``
                (eg. ```script.sh params.yml```).

                .. note ::

                    `Experiment` objects are able to automatically generate script.sh files that look like this::

                        #!/bin/bash
                        # File generated on the 06:34PM September 13, 2019
                        # GIT:
                        # - repo /path/to/project/module/
                        # - remote {/path/to/project/module/url}
                        # - commit 51ad1eae73a2082e7636056fcd06e112d3fbca9c

                        export PYTHONPATH="${PYTHONPATH}:path/to/project"
                        python /path/to/project/module/experiment.py --call ${1}

            * A set of experiment folders representing individual experiments within which each experiment has a
              ``params.yml`` with a set of override parameters changed from the original defaults. These overrides define the
              unique name of each experiment (in the case that multiple experiments share the same overrides each experiment
              folder is additionally numbered after the first instantiation). Additionally, each params.yml contains the
              following::

                    # Parameters special to params.yml
                    root: /path/to/root  #  The root directory to which the experiment belongs (should not be set)
                    name: a:10__b:3 #  The name of the experiment (should not be set)
                    status: registered #  The status of the experiment (one of ['registered' | 'running' | 'error' | 'finished'])
                    created: 07:41PM September 16, 2019 #  The date the experiment was created (should not be set)
                    purpose: this is an experiment example #  The purpose for the experiment (should not be set)
                    messages: {} #  A dictionary of messages which are able to vary throughout the experiment (should not be set)

                    # git is updated to the git information at registration if ``definition['path']`` exists in
                    # the defaults.yml file
                    git: #  A dictionary containing the git history corresponding to the defaults.yml file. Only
                      local_path: path/to/git/repo/params/were/defined/in
                      remote_url: /remote/repo/url
                      hash: 80dcfd98e6c3c17e1bafa72ee56744d4a6e30e80
                    definition:
                       path: /path/to/module   # Path to module where experiment was generated
                       class: NameOfExperimentClass     # Name of experiment class params are compatible with

                    # Parameters from the default (with values overridden)
                    a: 3 #  This is the first parameter (default=3)
                    b: '4' #  This is the second parameter (default='4')

            * ``experiment.yml`` preserves the experiment state with the following entries::

                    root: /path/to/root
                    defaults: /path/to/root/defaults.yml
                    script: /path/to/root/script.sh
                    experiments:
                    - /private/tmp/new-test/a:10__b:3
                    - /private/tmp/new-test/a:20__b:3
                    - /private/tmp/new-test/a:10__b:3_1
                    overides:
                    - a: 10
                      b: '3'
                    - a: 20
                      b: '3'
                    - a: 10
                      b: '3'
                    created: 07:41PM September 16, 2019

        The ``ExperimentManager`` provides the following public interface for managing experiments:

            * ``__init__(root)``:
                Link the experiment manager with a root directory and load the experiments.yml if it exists
            * ``initialise(script, defaults)``:
                Initialise an experiment set with a given script and default parameters
            * ``register(string_pattern)``:
                Register a number of experiments overriding parameters based on the particular ``string_pattern``
            * ``list()``:
                 Print all the experiments and their associated information
            * ``unlink(pattern)``:
                 Relieve the experiment manager of responsibility for all experiment names matching pattern
            * ``rm()``:
                 Delete any experiments which are no longer the responsibility of the experiment manager
            * ``run(string, options)``:
                 Run an experiment or all experiments (if string is ``'all'``) with options prepended.

        Example::

            experiment_manager = ExperimentManager(ROOT_PATH)   # Create experiment set in ROOT_PATH
            experiment_manager.initialise(PATH_TO_SCRIPT, PATH_TO_DEFAULTS)
            experiment_manager.register('parama: 1, paramb: [x, y]')   # Register a set of experiments
            experiment_manger.unlink('parama_1__paramb_y')                    # Remove an experiment
            experiment_manager.rm()                                    # Get rid of any experiments no longer managed
            experiment_run('parama:1__paramb:x', sh)                      # Run an experiment
            experiment_run('all')                                         # Run all created experiments
        """

    def __init__(self, root=""):
        """Link an experiment manager to root. If root already contains an ``experiment.yml`` then it is loaded.

        In order to link a new experiment with a defaults.yml and script.sh file then the initialise method must be
        called.

        Args:
            root: The root directory within which to create the experiment. If "" then the current working directory is
                used. If the root directory does not exist it will be made.

        Parameters:
            root: The root directory of the experiment manger
            defaults: A path to the defaults.yml file. Will be None for a fresh experiment manager (if experiments.yml
                has just been created).
            script: A path to the script.sh file. Will be None for a fresh experiment manager (if experiments.yml
                has just been created).
            # created: A string giving the date-time the experiment was created
            experiments: A list of paths to the experiments managed by the experiment manager
            overides: A list of dictionaries giving the names (keys) and values of the parameters overridden from the
                defaults for each experiment in experiments.
        """
        self.root = os.getcwd() if root == "" else os.path.expanduser(root)
        if not os.path.isdir(self.root):
            os.makedirs(self.root)
        self.defaults = None
        self.script = None
        # self.registered_date = None
        self.experiments = []
        self.overides = []
        self.created = None

        # Load dir from yaml
        if os.path.exists(os.path.join(self.root, 'experiment.yml')):
            self._from_yml()
        # else:
        #     print("{} is not currently an experiment folder. Call an initialise.".format(
        #         os.path.join(self.root, 'experiment.yml')))

    def check_initialised(self):
        """Make sure that ``'experiment.yml'``, ``'script.sh'``, ``'defaults.yml'`` all exist in the directory"""
        all_exist = all(
            [os.path.exists(os.path.join(self.root, s)) for s in ['experiment.yml', 'script.sh', 'defaults.yml']])
        if not all_exist:
            print('The current work directory is not a valid experiment folder. It is either missing one of'
                             '"script.sh", "experiment.yml" or "defaults.yml" or the experiment.yml file is not valid')
            exit()
            # raise ValueError('The current work directory is not a valid experiment folder. It is either missing one of'
            #                  '"script.sh", "experiment.yml" or "defaults.yml" or the experiment.yml file is not valid')

    def load_defaults(self):
        """Load the ``defaults.yml`` file into a dictionary"""
        with open(os.path.join(self.root, 'defaults.yml'), 'r') as file:
            defaults = ruamel.yaml.load(file, ruamel.yaml.RoundTripLoader)
        return defaults

    def save_params(self, params, experiment_name):
        """Save a dictionary of parameters at ``{root}/{experiment_name}/params.yml``

        Args:
            params (dict): A dictionary of parameters to be saved. Can also be a CommentedMap from ruamel
            experiment_name (str): The name of the experiment
        """
        experiment_path = os.path.join(self.root, experiment_name)
        with open(os.path.join(experiment_path, 'params.yml'), 'w') as out:
            yaml = ruamel.yaml.YAML()
            yaml.dump(params, out)

    def load_params(self, experiment_path, experiment_name=False):
        """Load parameters for an experiment. If ``experiment_name`` is True then experiment_path is assumed to be a
        path to the folder of the experiment else it is assumed to be a path to the ``params.yml`` file."""
        if experiment_name:
            experiment_path = os.path.join(self.root, experiment_path)
        with open(os.path.join(experiment_path, 'params.yml'), 'r') as params_yml:
            params = ruamel.yaml.load(params_yml, ruamel.yaml.RoundTripLoader)
        return params

    def _to_yml(self):
        """Save the current experiment manager to an ``experiment.yaml``"""
        params = {k: v for k, v in self.__dict__.items() if k[0] != '_' and not hasattr(v, '__call__')}
        with open(os.path.join(self.root, 'experiment.yml'), 'w') as file:
            ruamel.yaml.dump(params, file, Dumper=ruamel.yaml.RoundTripDumper)

    def _from_yml(self):
        """Load an experiment manager from an ``experiment.yml`` file"""
        with open(os.path.join(self.root, 'experiment.yml'), 'r') as file:
            params = ruamel.yaml.load(file, ruamel.yaml.RoundTripLoader)
            self.root = params['root']
            self.defaults = params['defaults']
            self.script = params['script']
            self.created = params['created']
            self.experiments = params['experiments']
            self.overides = params['overides']

    def initialise(self, defaults="", script=""):
        """Link an experiment manager with a ``defaults.yml`` file and ``sript.sh``.

        Args:
            defaults (str): A path to a ``defaults.yml``. If "" then a ``defaults.yml`` is searched for in the current
                work directory.
            script (str): A path to a ``script.sh``. If ``""`` then a script.sh file is searched for in the current work
                directory.
        """
        # Load defaults
        self.defaults = os.path.join(self.root, 'defaults.yml') if defaults == "" else defaults
        if os.path.exists(self.defaults):
            if defaults != "":
                copyfile(self.defaults, os.path.join(self.root, 'defaults.yml'))
        else:
            raise ValueError(f"No defaults.yml file exists in {self.root}. Either use the root argument to copy "
                             f"a default file from another location or add a 'defaults.yml' to the root directory"
                             f"manually.")

        # Load script file
        self.script = os.path.join(self.root, 'script.sh') if script == "" else script
        if os.path.exists(self.script):
            if script != "":
                copyfile(self.script, os.path.join(self.root, 'script.sh'))
        else:
            raise ValueError(f"File {self.script} does not exist. Either use the script argument to copy "
                             f"a script file from another location or add a 'script.sh' to the root directory"
                             f"manually.")
        self.script = os.path.join(self.root, 'script.sh')

        # Meta Information
        self.created = datetime.datetime.now().strftime("%I:%M%p %B %d, %Y")

        # Save state to yml
        if os.path.exists(os.path.join(self.root, 'experiment.yml')):
            print(f"There already exists a experiment.yml file in the root directory {self.root}. "
                  f"To reinitialise an experiment folder remove the experiment.yml.")
            exit()
        self._to_yml()

    def _generate_params_from_string_params(self, x):
        """Take as input a dictionary and convert the dictionary to a list of keys and a list of list of values
        len(values) = number of parameters specified whilst len(values[i]) = len(keys).
        """
        values = [[]]  # List of lists. Each inner list is of length keys
        keys = []

        for k, v in x.items():
            if type(v) is str:
                if '|' in v:
                    v = v.split('|')
                    v = [ruamel.yaml.load(e, Loader=ruamel.yaml.Loader) for e in v]
                else:
                    v = [v]
            else:
                v = [v]
            keys += [k]

            new_values = []
            # Generate values
            for val in values:  # val has d_type list
                for vv in v:  # vv has d_type string
                    # print(val, vv)
                    new_values += [val + [vv]]
            values = new_values
        return values, keys

    def register(self, string_params, purpose):
        """Register a set of experiments with the experiment manager.

        Experiments are created by passing a yaml dictionary string of parameters to overload in the ``params.yml``
        file. The special symbol ``'|'`` can be thought of as an or operator. When encountered each of the parameters
        either side ``'|'`` will be created separately with all other parameter combinations.

        Args:
            string_params (str): A yaml dictionary of parameters to override of the form
                ``'{p1: val11 | val12, p2: val2, p3: val2 | p4: val31 | val32 | val33, ...}'``.
                The type of each parameter is inferred from its value in defaults. A ValueError will be raised if any
                of the parameter cannot be found in defaults. Parameters can be float (1.), int (1), str (a), None, and
                dictionaries {a: 1., 2.} or lists [1, 2] of these types. None parameters are specified using empty space.
                The length of list parameters must match the length of the parameter in default.
                Dictionary parameters may only be partially defined. Missing keys will be assumed
                to take there default value.

                The special character '|' is used as an or operator. All combinations of parameters
                either side of an | operator will be created as separate experiments. In the example above
                ``N = 2 * 2 * 3 = 12`` experiments will be generated representing all the possible values for
                parameters ``p1``, ``p3`` and ``p4`` can take with ``p2`` set to ``val2`` for all.
            purpose (str): An optional purpose message for the experiment.

        .. note ::

            This function is currently only able to register list or dictionary parameters at the first level.
            ``{a: {a: 1.. b: 2.} | {a: 2.. b: 2.}}`` works creating two experiments with overloadeded dicts in each case
            but ``{a: {a: 1. | 2.,  b:2.}}`` will fail.
        """
        # TODO: This function is currently able to register or arguments only at the first level
        self.check_initialised()
        defaults = self.load_defaults()

        # Convert input string to dictionary
        p = ruamel.yaml.load(string_params, Loader=ruamel.yaml.Loader)

        values, keys = self._generate_params_from_string_params(p)

        # Add new experiments
        for elem in values:
            overides = {}
            for k, v in zip(keys, elem):
                if v is dict:
                    if defaults[k] is not dict:
                        raise ValueError(f'Attempting to update dictionary parameters but key {k} is not a dictionary'
                                         f'in the defaults.yml')
                    overides.update({k: defaults[k]})
                    for dict_k, dict_v in v.items():
                        if dict_k not in defaults[k]:
                            raise ValueError(f'key {dict_k} not found in defaults {k}')
                        overides[k].update({dict_k: type(defaults[k][dict_k])(dict_v)})
                if v is list:
                    if defaults[k] is not list:
                        raise ValueError(f'Attempting to update a list of parameters but key {k} does not have '
                                         f'a list value')
                    if len(v) != len(defaults[k]):
                        raise ValueError(f'Override list length does not match default list length')
                    overides.update({k: [type(defaults[k][i])(v[i]) for i in range(len(v))]})
                else:
                    overides.update({k: type(defaults[k])(v)})

            # Check parameters are in the defaults.yml file

            if any([k not in defaults for k in overides]):
                raise ValueError('Some of the specified keys were not found in the defaults')

            experiment_name = '__'.join([k + ':' + str(v) for k, v in overides.items()])
            experiment_path = os.path.join(self.root, experiment_name)

            # Setup experiment folder
            if os.path.isdir(experiment_path):
                for i in range(1, 100):
                    if not os.path.isdir(experiment_path + f"_{i}"):
                        # logging.info(f"Directory already exists creating. The current experiment will be set up "
                        #              f"at {experiment_path}_{i}")
                        experiment_name = experiment_name + f"_{i}"
                        experiment_path = experiment_path + f"_{i}"
                        break
                    if i == 99:
                        raise ValueError('The number of experiments allowed with the same overides is limited to 100')
            os.makedirs(experiment_path)

            # Convert defaults to params
            # definition = defaults['definition'] if 'definition' in defaults else None
            if defaults['version'] is not None:
                version = defaults['version']
                if 'path' in version:
                    version = get_version(path=version['path'])
                elif 'module' in version and 'class' in version:
                    # We want to get version form the original class if possible
                    spec = importlib.util.spec_from_file_location("_em." + version['class'], version['module'])
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    cls = getattr(module, version['class'])
                    version = get_version(cls=cls)
            else:
                version = None
            extra_params = {
                'root': self.root,
                'name': experiment_name,
                'status': 'registered',
                'created': datetime.datetime.now().strftime("%I:%M%p %B %d, %Y"),
                'purpose': purpose,
                'messages': {},
                'version': version}

            params = copy.deepcopy(defaults)
            # Remove optional parameters from defaults
            for k in ['created', 'version']:
                if k in params:
                    params.pop(k)

            # Add base parameters to params
            helps = get_attribute_helps(Experiment)
            for i, (k, v) in enumerate(extra_params.items()):
                h = helps[k].split(':')[1] if helps[k] is not None else helps[k]
                params.insert(i, k, v, h)

            # Update the overridden parameters
            params.update(overides)
            self.save_params(params, experiment_name)
            self.experiments.append(experiment_path)
            self.overides.append(overides)
            self._to_yml()

    def list(self):
        """List all experiments currently created with the experiment manager."""
        self.check_initialised()
        # Construct dictionary
        if self.experiments != []:
            table = {'overrides': [], 'purpose': [], 'created': [], 'status': [], 'messages': [], 'commit': []}
            keys = ['name', 'purpose', 'created', 'status', 'messages', 'version']
            for i, p in enumerate(self.experiments):
                P = self.load_params(p)
                for k_table, k_params in zip(table, keys):
                    if k_params == 'version':
                        if 'git' in P[k_params]:
                            v = P[k_params]['git']['commit']
                        else:
                            v = None
                        # print(P[k_params].keys())
                        # v = P[k_params]['comit']
                    else:
                        v = P[k_params]
                    table[k_table] += [v]
            table = pd.DataFrame(table)
            # with pd.option_context('display.max_rows', None, 'display.max_columns',
            #                        None):  # more options can be specified also
            print(table)
        else:
            print('No experiments currently registered!')
        print()
        defaults = self.load_defaults()
        # print(defaults)
        if 'created' in defaults:
            print(f'Defaults: \n'
                  f'- created {defaults["created"]}')
        if 'version' in defaults:
            version = defaults["version"]
            if 'path' in version:
                print(f'- path {version["path"]}')
            else:
                print(f'- module {version["module"]}')
                print(f'- class {version["class"]}')
            if 'git' in version:
                git = version['git']
                print('- git:')
                print(f'   local: {git["local"]}')
                print(f'   commit: {git["commit"]}')
                print(f'   remote: {git["remote"]}')

    def rm(self):
        """Remove directories no longer linked to the experiment manager"""
        self.check_initialised()
        subdirs = [x[0] for x in os.walk(self.root) if x[0] != self.root and x[0] not in self.experiments]
        for d in subdirs:
            print(d)
            rmtree(d)

    def run(self, pattern, *args):
        """Run all experiments that match the global pattern using the run command given by args."""
        experiments = [p for p in glob.glob(os.path.join(self.root, pattern)) if p in self.experiments]
        for p in experiments:
            P = self.load_params(p)
            if P['status'] == 'registered':
                args = list(args)
                subprocess_args = args + [self.script, os.path.join(p, 'params.yml')]
                print('\nRunning: {}'.format(" ".join(subprocess_args)))
                subprocess.call(args + [self.script, os.path.join(p, 'params.yml')])
                time.sleep(1)

    def unlink(self, pattern='*'):
        """Unlink all experiments matching pattern. Does not delete experiment folders simply deletes the experiment
        paths from the experiment folder. To delete call method ``rm``."""
        self.check_initialised()
        remove_paths = [p for p in glob.glob(os.path.join(self.root, pattern)) if p in self.experiments]
        if len(remove_paths) != 0:
            print("Removing Experiments...")
            for p in remove_paths:
                print(p)
                self.experiments.remove(p)
            self._to_yml()
            print("Note models are removed from the experiment list only. To remove the model directories run"
                  "experiment rm")
        else:
            print(f"No experiments match pattern {pattern}")

    def relink(self, pattern='*'):
        """Re-link all experiment folders that match ``pattern`` (and are not currently managed by the experiment
        manager)"""
        self.check_initialised()
        subdirs = [x[0] for x in os.walk(self.root)
                   if x[0] != self.root
                   and x[0] in glob.glob(os.path.join(self.root, pattern))
                   and x[0] not in self.experiments]
        if len(subdirs) == 0:
            print("No experiements to link that match pattern and aren't managed already")

        for d in subdirs:
            params, defaults = self.load_params(d, True), self.load_defaults()
            if any([k not in defaults for k in params]):
                print(f'Cannot re-link folder {d} as params are not compatible with defaults')
            else:
                print(f'Relinking {d}')
                self.experiments += [d]
                self.overides += [{pk: pv for pk, pv in params.items() if defaults[pk] != pv}]
        self._to_yml()


def main(argv):
    mode = argv[1]
    experiment_manager = ExperimentManager()
    if mode == "init":
        init_args = ['', '']  # [defaults, script]
        for p in argv[2:]:
            if '.yml' in os.path.basename(p):
                init_args[0] = p
            elif '.sh' in os.path.basename(p):
                init_args[1] = p
            else:
                raise ValueError(f'Path {p} is not to either a params.yml file or script.sh file')
        experiment_manager.initialise(*init_args)

    elif mode == 'register':  # --params --purpose
        if len(argv) != 4:
            raise ValueError('Missing parameter string or purpose message. For register the call should be experiment '
                             'register {PARAM_STRING} {PURPOSE}')
        # print("Enter purpose for the current experiment:")
        # purpose = input()
        experiment_manager.register(argv[2], argv[3])

    elif mode == 'list':
        experiment_manager.list()

    elif mode == 'rm':
        experiment_manager.rm()

    elif mode == 'run':
        if len(argv) < 3:
            raise ValueError("Missing experiment name to run. (To run all experiments pass 'all')")
        elif len(argv) == 3:
            experiment_manager.run(argv[2])
        elif len(argv) == 4:
            options = ruamel.yaml.load(argv[3], Loader=ruamel.yaml.Loader)
            if type(options) is not list:
                options = [options]
            experiment_manager.run(argv[2], *options)

    elif mode == 'unlink':
        if len(argv) != 3:
            raise ValueError("Missing experiment name argument")
        pattern = argv[2]
        experiment_manager.unlink(pattern)

    elif mode == 'help':
        help(ExperimentManager)

    elif mode == 'relink':
        pattern = argv[2]
        experiment_manager.relink(pattern)


if __name__ == "__main__":
    main(sys.argv)
