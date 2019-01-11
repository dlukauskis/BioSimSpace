######################################################################
# BioSimSpace: Making biomolecular simulation a breeze!
#
# Copyright: 2017-2019
#
# Authors: Lester Hedges <lester.hedges@gmail.com>
#
# BioSimSpace is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with BioSimSpace. If not, see <http://www.gnu.org/licenses/>.
#####################################################################

"""
Functionality for running simulations with GROMACS.
Author: Lester Hedges <lester.hedges@gmail.com>
"""

import Sire.Base as _SireBase
import Sire.IO as _SireIO

from BioSimSpace import _gmx_exe
from . import _process
from .._Exceptions import MissingSoftwareError as _MissingSoftwareError
from .._SireWrappers import System as _System
from ..Trajectory import Trajectory as _Trajectory

import BioSimSpace.Protocol as _Protocol
import BioSimSpace.Types._type as _Type
import BioSimSpace.Units as _Units
import BioSimSpace._Utils as _Utils

import os as _os
import pygtail as _pygtail
import subprocess as _subprocess
import timeit as _timeit
import warnings as _warnings

__all__ = ["Gromacs"]

class Gromacs(_process.Process):
    """A class for running simulations using GROMACS."""

    def __init__(self, system, protocol, exe=None, name="gromacs",
            work_dir=None, seed=None, property_map={}):
        """Constructor.


           Positional arguments
           --------------------

           system : BioSimSpace._SireWrappers.System
               The molecular system.

           protocol : BioSimSpace.Protocol
               The protocol for the GROMACS process.


           Keyword arguments
           -----------------

           exe : str
               The full path to the GROMACS executable.

           name : str
               The name of the process.

           work_dir :
               The working directory for the process.

           seed : int
               A random number seed.

           property_map : dict
               A dictionary that maps system "properties" to their user defined
               values. This allows the user to refer to properties with their
               own naming scheme, e.g. { "charge" : "my-charge" }
        """

        # Call the base class constructor.
        super().__init__(system, protocol, name, work_dir, seed, property_map)

        # Set the package name.
        self._package_name = "GROMACS"

        # This process can generate trajectory data.
        self._has_trajectory = True

        if _gmx_exe is not None:
            self._exe = _gmx_exe
        else:
            if exe is not None:
                # Make sure executable exists.
                if _os.path.isfile(exe):
                    self._exe = exe
                else:
                    raise IOError("GROMACS executable doesn't exist: '%s'" % exe)
            else:
                raise _MissingSoftwareError("'BioSimSpace.Process.Gromacs' is not supported. "
                                            "Please install GROMACS (http://www.gromacs.org).")

        # Initialise the stdout dictionary and title header.
        self._stdout_dict = _process._MultiDict()

        # Store the name of the GROMACS log file.
        self._log_file = "%s/%s.log" % (self._work_dir, name)

        # The names of the input files.
        self._gro_file = "%s/%s.gro" % (self._work_dir, name)
        self._top_file = "%s/%s.top" % (self._work_dir, name)

        # Set the path for the GROMACS configuration file.
        self._config_file = "%s/%s.mdp" % (self._work_dir, name)

        # Create the list of input files.
        self._input_files = [self._config_file, self._gro_file, self._top_file]

        # Now set up the working directory for the process.
        self._setup()

    def _setup(self):
        """Setup the input files and working directory ready for simulation."""

        # Create the input files...

        # GRO87 file.
        gro = _SireIO.Gro87(self._system, self._property_map)
        gro.writeToFile(self._gro_file)

        # TOP file.
        top = _SireIO.GroTop(self._system, self._property_map)
        top.writeToFile(self._top_file)

        # Create the binary input file name.
        self._tpr_file = "%s/%s.tpr" % (self._work_dir, self._name)
        self._input_files.append(self._tpr_file)

        # Generate the GROMACS configuration file.
        # Skip if the user has passed a custom config.
        if type(self._protocol) is _Protocol.Custom:
            self.setConfig(self._protocol.getConfig())
        else:
            self._generate_config()
        self.writeConfig(self._config_file)

        # Generate the dictionary of command-line arguments.
        self._generate_args()

        # Return the list of input files.
        return self._input_files

    def _generate_config(self):
        """Generate GROMACS configuration file strings."""

        # Clear the existing configuration list.
        self._config = []

        # Check whether the system contains periodic box information.
        # For now, well not attempt to generate a box if the system property
        # is missing. If no box is present, we'll assume a non-periodic simulation.
        if "space" in self._system.propertyKeys():
            has_box = True
        else:
            _warnings.warn("No simulation box found. Assuming gas phase simulation.")
            has_box = False

        # The list of configuration strings.
        # We don't repeatedly call addToConfig since this will run grommp
        # to re-compile the binary run input file each time.
        config = []

        # While the configuration parameters below share a lot of overlap,
        # we choose the keep them separate so that the user can modify options
        # for a given protocol in a single place.

        # Add configuration variables for a minimisation simulation.
        if type(self._protocol) is _Protocol.Minimisation:
            config.append("integrator = steep")         # Use steepest descent.
            config.append("nsteps = %d"
                % self._protocol.getSteps())            # Set the number of steps.
            config.append("nstxout = %d"
                % self._protocol.getSteps())            # Only write the final coordinates.
            config.append("cutoff-scheme = Verlet")     # Use Verlet pair lists.
            config.append("ns-type = grid")             # Use a grid to search for neighbours.
            if has_box:
                config.append("pbc = xyz")              # Simulate a fully periodic box.
            config.append("coulombtype = PME")          # Fast smooth Particle-Mesh Ewald.
            config.append("DispCorr = EnerPres")        # Dispersion corrections for energy and pressure.

        # Add configuration variables for an equilibration simulation.
        elif type(self._protocol) is _Protocol.Equilibration:
            config.append("integrator = steep")         # Use steepest descent.
            config.append("nsteps = 1000")
            config.append("cutoff-scheme = Verlet")     # Use Verlet pair lists.
            config.append("ns-type = grid")             # Use a grid to search for neighbours.
            config.append("pbc = xyz")                  # Simulate a fully periodic box.
            config.append("coulombtype = PME")          # Fast smooth Particle-Mesh Ewald.
            config.append("DispCorr = EnerPres")        # Dispersion corrections for energy and pressure.

            # Restrain backbone atoms in all non-water or ion molecules.
            if self._protocol.isRestrained():

                # Copy the user property map.
                property_map = self._property_map.copy()

                # Parse the topology in serial to ensure that molecules are
                # ordered correctly. Don't sort based on name.
                property_map["parallel"] = _SireBase.wrap(False)
                property_map["sort"] = _SireBase.wrap(False)

                # Create a GROMACS topology object.
                top = _SireIO.GroTop(self._system, property_map)

                # Get the top file as a list of lines.
                top_lines = top.lines()

                # List of 'moleculetype' record indices.
                moleculetypes_idx = []

                # Store the line index for the start of each 'moleculetype' record.
                for idx, line in enumerate(top_lines):
                    if "[ moleculetype ]" in line:
                        moleculetypes_idx.append(idx)

                # Extract all of the molecules from the system.
                mols = _System(self._system).getMolecules()

                # The number of restraint files.
                num_restraint = 1

                # Loop over all of the molecules and create a constraint file for
                # each, excluding any water molecules or ions.
                for idx, mol in enumerate(mols):
                    if not mol.isWater() and mol.nAtoms() > 1:
                        # Create a GRO file from the molecule.
                        gro = _SireIO.Gro87(mol.toSystem()._sire_system)

                        # Create the name of the temporary gro file.
                        gro_file = "%s/tmp.gro" % self._work_dir

                        # Write to a temporary file.
                        gro.writeToFile(gro_file)

                        # Create the name of the restrant file.
                        restraint_file = "%s/posre_%04d.itp" % (self._work_dir, num_restraint)

                        # Use genrestr to generate a restraint file for the molecule.
                        command = "echo Backbone | %s genrestr -f %s -o %s" % (self._exe, gro_file, restraint_file)

                        # Run the command.
                        proc = _subprocess.run(command, shell=True,
                            stdout=_subprocess.PIPE, stderr=_subprocess.PIPE)

                        # Check that grompp ran successfully.
                        if proc.returncode != 0:
                            raise RuntimeError("Unable to generate GROMACS restraint file.")

                        # Include the position restraint file in the correct place within
                        # the topology file. We put the additional include directove at the
                        # end of the block so we move to the line before the next moleculetype
                        # record.
                        new_top_lines = top_lines[:moleculetypes_idx[idx+1]-1]

                        # Append the additional information.
                        new_top_lines.append('#include "%s"' % restraint_file)
                        new_top_lines.append("")

                        # Now extend with the remainder of the file.
                        new_top_lines.extend(top_lines[moleculetypes_idx[idx+1]:])

                        # Overwrite the topology file lines.
                        top_lines = new_top_lines

                        # Increment the number of restraint files.
                        num_restraint += 1

                        # Append the restraint file to the list of autogenerated inputs.
                        self._input_files.append(restraint_file)

                # Write the updated topology to file.
                with open(self._top_file, "w") as file:
                    for line in top_lines:
                        file.write("%s\n" % line)

                # Remove the temporary run file.
                if _os.path.isfile(gro_file):
                    _os.remove(gro_file)

        else:
            raise NotImplementedError("Only 'minimisation' protocol is currently supported.")

        # Set the configuration.
        self.setConfig(config)

    def _generate_args(self):
        """Generate the dictionary of command-line arguments."""

        # Clear the existing arguments.
        self.clearArgs()

        # Add the default arguments.
        self.setArg("mdrun", True)          # Use mdrun.
        self.setArg("-v", True)             # Verbose output.
        self.setArg("-deffnm", self._name)  # Output file prefix.

    def _generate_binary_run_file(self):
        """Use grommp to generate the binary run input file."""

        # Use grompp to generate the portable binary run input file.
        command = "%s grompp -f %s -po %s.out.mdp -c %s -p %s -r %s -o %s" \
            % (self._exe, self._config_file, self._config_file.split(".")[0],
                self._gro_file, self._top_file, self._gro_file, self._tpr_file)

        # Run the command.
        proc = _subprocess.run(command, shell=True,
            stdout=_subprocess.PIPE, stderr=_subprocess.PIPE)

        # Check that grompp ran successfully.
        if proc.returncode != 0:
            raise RuntimeError("Unable to generate GROMACS binary run input file.")

    def addToConfig(self, config):
        """Add a string to the configuration list.


           Positional arguments
           --------------------

           config : str, [ str ]
               A configuration string, a list of configuration strings, or a
               path to a configuration file.
        """

        # Call the base class method.
        super().addToConfig(config)

        # Use grompp to generate the portable binary run input file.
        self._generate_binary_run_file()

    def resetConfig(self):
        """Reset the configuration parameters."""
        self._generate_config()

        # Use grompp to generate the portable binary run input file.
        self._generate_binary_run_file()

    def setConfig(self, config):
        """Set the list of configuration file strings.


           Positional arguments
           --------------------

           config : str, [ str ]
               The list of configuration strings, or a path to a configuration
               file.
        """

        # Call the base class method.
        super().setConfig(config)

        # Use grompp to generate the portable binary run input file.
        self._generate_binary_run_file()

    def start(self):
        """Start the GROMACS process."""

        # The process is currently queued.
        if self.isQueued():
            return

        # Process is already running.
        if self._process is not None:
            if self._process.isRunning():
                return

        # Clear any existing output.
        self._clear_output()

        # Run the process in the working directory.
        with _Utils.cd(self._work_dir):

            # Create the arguments string list.
            args = self.getArgStringList()

            # Write the command-line process to a README.txt file.
            with open("README.txt", "w") as f:

                # Set the command-line string.
                self._command = "%s " % self._exe + self.getArgString()

                # Write the command to file.
                f.write("# GROMACS was run with the following command:\n")
                f.write("%s\n" % self._command)

            # Start the timer.
            self._timer = _timeit.default_timer()

            # Start the simulation.
            self._process = _SireBase.Process.run(self._exe, args,
                "%s.out" % self._name, "%s.out" % self._name)

            # For historical reasons (console message aggregation with MPI), Gromacs
            # writes the majority of its output to stderr. For user convenience, we
            # redirect all output to stdout, and place a message in the stderr file
            # to highlight this.
            with open(self._stderr_file, "w") as f:
                f.write("All output has been redirected to the stdout stream!\n")

        return self

    def getSystem(self, block="AUTO"):
        """Get the latest molecular system.

           Keyword arguments
           -----------------

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           system : BioSimSpace._SireWrappers.System
               The latest molecular system.
        """

        # Wait for the process to finish.
        if block is True:
            self.wait()
        elif block == "AUTO" and self._is_blocked:
            self.wait()

        # Create the name of the restart GRO file.
        restart = "%s/%s.gro" % (self._work_dir, self._name)

        # Check that the file exists.
        if _os.path.isfile(restart):
            # Create and return the molecular system.
            return _System(_SireIO.MoleculeParser.read([restart, self._top_file], self._property_map))

        else:
            return None

    def getCurrentSystem(self):
        """Get the latest molecular system.

           Returns
           -------

           system : BioSimSpace._SireWrappers.System
               The latest molecular system.
        """
        return self.getSystem(block=False)

    def getRecord(self, record, time_series=False, unit=None, block="AUTO"):
        """Get a record from the stdout dictionary.


           Positional arguments
           --------------------

           record : str
               The record key.


           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           unit : BioSimSpace.Types.Type
               The unit to convert the record to.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           record :
               The matching record.
        """

        # Wait for the process to finish.
        if block is True:
            self.wait()
        elif block == "AUTO" and self._is_blocked:
            self.wait()

        self._update_stdout_dict()
        return self._get_stdout_record(record, time_series, unit)

    def getCurrentRecord(self, record, time_series=False, unit=None):
        """Get a current record from the stdout dictionary.


           Positional arguments
           --------------------

           record : str
               The record key.


           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           unit : BioSimSpace.Types.Type
               The unit to convert the record to.

           Returns
           -------

           record :
               The matching record.
        """
        self._update_stdout_dict()
        return self._get_stdout_record(record, time_series, unit)

    def getRecords(self, block="AUTO"):
        """Return the dictionary of stdout time-series records.

           Keyword arguments:

           block       -- Whether to block until the process has finished running.
        """
        # Wait for the process to finish.
        if block is True:
            self.wait()
        elif block == "AUTO" and self._is_blocked:
            self.wait()

        return self._stdout_dict.copy()

    def getCurrentRecords(self):
        """Return the current dictionary of stdout time-series records.

           Keyword arguments
           -----------------

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           records : BioSimSpace.Process._process._MultiDict
              The dictionary of time-series records.
        """
        return getRecords(block=False)

    def getTime(self, time_series=False, block="AUTO"):
        """Get the time (in nanoseconds).

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           time : BioSimSpace.Types.Time
               The current simulation time in nanoseconds.
        """

        if type(self._protocol) is _Protocol.Minimisation:
            return None

        else:
            return self.getRecord("TIME", time_series, _Units.Time.nanosecond, block)

    def getCurrentTime(self, time_series=False):
        """Get the current time (in nanoseconds).

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           time : BioSimSpace.Types.Time
               The current simulation time in nanoseconds.
        """
        return self.getTime(time_series, block=False)

    def getStep(self, time_series=False, block="AUTO"):
        """Get the number of integration steps.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           step : int
               The current number of integration steps.
        """
        return self.getRecord("STEP", time_series, None, block)

    def getCurrentStep(self, time_series=False):
        """Get the current number of integration steps.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           step : int
               The current number of integration steps.
        """
        return self.getStep(time_series, block=False)

    def getBondEnergy(self, time_series=False, block="AUTO"):
        """Get the bond energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The bond energy.
        """
        return self.getRecord("BOND", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentBondEnergy(self, time_series=False):
        """Get the current bond energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The bond energy.
        """
        return self.getBondEnergy(time_series, block=False)

    def getAngleEnergy(self, time_series=False, block="AUTO"):
        """Get the angle energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The angle energy.
        """
        return self.getRecord("ANGLE", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentAngleEnergy(self, time_series=False):
        """Get the current angle energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The angle energy.
        """
        return self.getAngleEnergy(time_series, block=False)

    def getDihedralEnergy(self, time_series=False, block="AUTO"):
        """Get the dihedral energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The dihedral energy.
        """
        return self.getRecord("PROPERDIH", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentDihedralEnergy(self, time_series=False):
        """Get the current dihedral energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The dihedral energy.
        """
        return self.getDihedralEnergy(time_series, block=False)

    def getImproperEnergy(self, time_series=False, block="AUTO"):
        """Get the improper energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The improper energy.
        """
        return self.getRecord("IMPRPROPERDIH", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentImproperEnergy(self, time_series=False):
        """Get the current improper energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The improper energy.
        """
        return self.getImproperEnergy(time_series, block=False)

    def getLennardJones14(self, time_series=False, block="AUTO"):
        """Get the Lennard-Jones energy between atoms 1 and 4.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The Lennard-Jones energy.
        """
        return self.getRecord("LJ14", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentLennardJones14(self, time_series=False):
        """Get the current Lennard-Jones energy between atoms 1 and 4.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The Lennard-Jones energy.
        """
        return self.getLennardJones14(time_series, block=False)

    def getLennardJonesSR(self, time_series=False, block="AUTO"):
        """Get the short-range Lennard-Jones energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The short-range Lennard-Jones energy.
        """
        return self.getRecord("LJSR", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentLennardJonesSR(self, time_series=False):
        """Get the current short-range Lennard-Jones energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The Lennard-Jones energy.
        """
        return self.getLennardJonesSR(time_series, block=False)

    def getCoulomb14(self, time_series=False, block="AUTO"):
        """Get the Coulomb energy between atoms 1 and 4.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The Coulomb energy.
        """
        return self.getRecord("COULOMB14", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentCoulomb14(self, time_series=False):
        """Get the current Coulomb energy between atoms 1 and 4.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The Coulomb energy.
        """
        return self.getCoulomb14(time_series, block=False)

    def getCoulombSR(self, time_series=False, block="AUTO"):
        """Get the short-range Coulomb energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The Coulomb energy.
        """
        return self.getRecord("COULOMBSR", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentCoulombSR(self, time_series=False):
        """Get the current short-range Coulomb energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The Coulomb energy.
        """
        return self.getCoulombSR(time_series, block=False)

    def getCoulombReciprocal(self, time_series=False, block="AUTO"):
        """Get the reciprocal space Coulomb energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The Coulomb energy.
        """
        return self.getRecord("COULRECIP", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentCoulombReciprocal(self, time_series=False):
        """Get the current reciprocal space Coulomb energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The Coulomb energy.
        """
        return self.getCoulombReciprocal(time_series, block=False)

    def getDispersionCorrection(self, time_series=False, block="AUTO"):
        """Get the dispersion correction.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The dispersion correction.
        """
        return self.getRecord("DISPERCORR", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentDispersionCorrection(self, time_series=False):
        """Get the current dispersion correction.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The dispersion correction.
        """
        return self.getDispersionCorrection(time_series, block=False)

    def getRestraintEnergy(self, time_series=False, block="AUTO"):
        """Get the position restraint energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The dispersion correction.
        """
        return self.getRecord("POSITIONREST", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentRestraintEnergy(self, time_series=False):
        """Get the current position restraint energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The dispersion correction.
        """
        return self.getRestraintEnergy(time_series, block=False)

    def getPotentialEnergy(self, time_series=False, block="AUTO"):
        """Get the potential energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The potential energy.
        """
        return self.getRecord("POTENTIAL", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentPotentialEnergy(self, time_series=False):
        """Get the current potential energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The potential energy.
        """
        return self.getPotentialEnergy(time_series, block=False)

    def getKinetecEnergy(self, time_series=False, block="AUTO"):
        """Get the kinetic energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The kinetic energy.
        """
        return self.getRecord("KINETICEN", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentKineticEnergy(self, time_series=False):
        """Get the current kinetic energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The kinetic energy.
        """
        return self.getKineticEnergy(time_series, block=False)

    def getTotalEnergy(self, time_series=False, block="AUTO"):
        """Get the total energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The total energy.
        """
        return self.getRecord("TOTALENERGY", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentTotalEnergy(self, time_series=False):
        """Get the current total energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The total energy.
        """
        return self.getTotalEnergy(time_series, block=False)

    def getConservedEnergy(self, time_series=False, block="AUTO"):
        """Get the conserved energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The conserved energy.
        """
        return self.getRecord("CONSERVEDEN", time_series, _Units.Energy.kj_per_mol, block)

    def getCurrentConservedEnergy(self, time_series=False):
        """Get the current conserved energy.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Energy
               The conserved energy.
        """
        return self.getConservedEnergy(time_series, block=False)

    def getTemperature(self, time_series=False, block="AUTO"):
        """Get the temperature.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Temperature
               The temperature.
        """
        return self.getRecord("TEMPERATURE", time_series, _Units.Temperature.kelvin, block)

    def getCurrentTemperature(self, time_series=False):
        """Get the current temperature.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Temperature
               The current temperature.
        """
        return self.getTemperature(time_series, block=False)

    def getPressure(self, time_series=False, block="AUTO"):
        """Get the pressure.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Pressure
               The pressure.
        """
        return self.getRecord("PRESSURE", time_series, _Units.Pressure.bar, block)

    def getCurrentPressure(self, time_series=False):
        """Get the current pressure.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Pressure
               The current pressure.
        """
        return self.getPressure(time_series, block=False)

    def getPressureDC(self, time_series=False, block="AUTO"):
        """Get the DC pressure.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Pressure
               The DC pressure.
        """
        return self.getRecord("PRESDC", time_series, _Units.Pressure.bar, block)

    def getCurrentPressureDC(self, time_series=False):
        """Get the current DC pressure.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Pressure
               The current pressure.
        """
        return self.getPressureDC(time_series, block=False)

    def getConstraintRMSD(self, time_series=False, block="AUTO"):
        """Get the RMSD of the constrained atoms.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.

           block : bool
               Whether to block until the process has finished running.


           Returns
           -------

           energy : BioSimSpace.Types.Length
               The constrained RMSD.
        """
        return self.getRecord("CONSTRRMSD", time_series, _Units.Length.nanometer, block)

    def getCurrentConstraintRMSD(self, time_series=False):
        """Get the current RMSD of the constrained atoms.

           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a list of time series records.


           Returns
           -------

           energy : BioSimSpace.Types.Length
               The current constrained RMSD.
        """
        return self.getConstraintRMSD(time_series, block=False)

    def stdout(self, n=10):
        """Print the last n lines of the stdout buffer.

           Keyword arguments
           -----------------

           n : int
               The number of lines to print.
        """

        # Note that thermodynamic records, e.g. energy, pressure, temperture,
        # are redirected to a log file.

        # Ensure that the number of lines is positive.
        if n < 0:
            raise ValueError("The number of lines must be positive!")

        # Append any new lines to the stdout list.
        for line in _pygtail.Pygtail(self._stdout_file):
            self._stdout.append(line.rstrip())

        # Get the current number of lines.
        num_lines = len(self._stdout)

        # Set the line from which to start printing.
        if num_lines < n:
            start = 0
        else:
            start = num_lines - n

        # Print the lines.
        for x in range(start, num_lines):
            print(self._stdout[x])

    def _update_stdout_dict(self):
        """Update the dictonary of thermodynamic records."""

        # A list of the new record lines.
        lines = []

        # Append any new lines.
        for line in _pygtail.Pygtail(self._log_file):
            lines.append(line)

        # Store the number of lines.
        num_lines = len(lines)

        # Line index counter.
        x = 0

        # Append any new records to the stdout dictionary.
        while x < num_lines:

            # We've hit any energy record section.
            if lines[x].strip() == "Energies (kJ/mol)":

                # Initialise lists to hold all of the key/value pairs.
                keys = []
                values = []

                # Loop until we reach a blank line, or the end of the lines.
                while True:

                    # End of file.
                    if x + 2 >= num_lines:
                        break

                    # Extract the lines with the keys and values.
                    k_line = lines[x+1]
                    v_line = lines[x+2]

                    # Empty line:
                    if len(k_line.strip()) == 0 or len(v_line.strip()) == 0:
                        break

                    # Add whitespace at the end so that the splitting algorithm
                    # below works properly.
                    k_line = k_line + " "
                    v_line = v_line + " "

                    # Set the starting index of a record.
                    start_idx = 0

                    # Create lists to hold the keys and values.
                    k = []
                    v = []

                    # Split the lines into the record headings and corresponding
                    # values.
                    for idx, val in enumerate(v_line):
                        # We've hit the end of the line.
                        if idx + 1 == len(v_line):
                            break

                        # This is the end of a record, i.e. we've gone from a
                        # character to whitespace. Record the key and value and
                        # update the start index for the next record.
                        if val != " " and v_line[idx+1] == " ":
                            k.append(k_line[start_idx:idx+1])
                            v.append(v_line[start_idx:idx+1])
                            start_idx=idx+1

                    # Update the keys and values, making sure the number of
                    # values matches the number of keys.
                    keys.extend(k)
                    values.extend(v[:len(k)])

                    # Update the line index.
                    x = x + 2

                # Add the records to the dictionary.
                if (len(keys) == len(values)):
                    for key, value in zip(keys, values):
                        # Replace certain characters in the key in order to make
                        # the formatting consistent.

                        # Convert to upper case.
                        key = key.upper()

                        # Strip whitespace and newlines from beginning and end.
                        key = key.strip()

                        # Remove whitespace.
                        key = key.replace(" ", "")

                        # Remove periods.
                        key = key.replace(".", "")

                        # Remove hyphens.
                        key = key.replace("-", "")

                        # Remove parentheses.
                        key = key.replace("(", "")
                        key = key.replace(")", "")

                        # Remove instances of BAR.
                        key = key.replace("BAR", "")

                        # Add the record.
                        self._stdout_dict[key] = value.strip()

            # This is a time record.
            elif "Step" in lines[x].strip():
                if x + 1 < num_lines:
                    records = lines[x+1].split()

                    # There should be two records, 'Step' and 'Time'.
                    if len(records) == 2:
                        self._stdout_dict["STEP"] = records[0].strip()
                        self._stdout_dict["TIME"] = records[1].strip()

                # Update the line index.
                x += 2

            # We've reached an averages section, abort.
            elif " A V E R A G E S" in lines[x]:
                break

            # No match, move to the next line.
            else:
                x += 1

    def _get_stdout_record(self, key, time_series=False, unit=None):
        """Helper function to get a stdout record from the dictionary.


           Positional arguments
           --------------------

           key : str
               The record key.


           Keyword arguments
           -----------------

           time_series : bool
               Whether to return a time series of records.

           unit : BioSimSpace.Types._type.Type
               The unit to convert the record to.

           Returns
           -------

           record :
               The matching stdout record.
        """

        # No data!
        if len(self._stdout_dict) is 0:
            return None

        if type(time_series) is not bool:
            _warnings.warn("Non-boolean time-series flag. Defaulting to False!")
            time_series = False

        # Valdate the unit.
        if unit is not None:
            if not isinstance(unit, _Type.Type):
                raise TypeError("'unit' must be of type 'BioSimSpace.Types'")

        # Return the list of dictionary values.
        if time_series:
            try:
                if key is "STEP":
                    return [int(x) for x in self._stdout_dict[key]]
                else:
                    if unit is None:
                        return [float(x) for x in self._stdout_dict[key]]
                    else:
                        return [float(x) * unit for x in self._stdout_dict[key]]

            except KeyError:
                return None

        # Return the most recent dictionary value.
        else:
            try:
                if key is "STEP":
                    return int(self._stdout_dict[key][-1])
                else:
                    if unit is None:
                        return float(self._stdout_dict[key][-1])
                    else:
                        return float(self._stdout_dict[key][-1]) * unit

            except KeyError:
                return None
