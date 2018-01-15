"""
@package biosimspace
@author  Lester Hedges
@brief   A class for running simulations using NAMD.
"""

from Sire.Base import findExe, Process
from Sire.IO import CharmmPSF, MoleculeParser, PDB2
from Sire.Maths import Vector

from . import process
from ..Protocol.protocol import Protocol, ProtocolType

from math import ceil, floor
from os import path
from timeit import default_timer as timer
from warnings import warn

import __main__ as main
import os

class NamdProcess(process.Process):
    """A class for running simulations using NAMD."""

    def __init__(self, system, protocol, exe=None, name="namd",
            work_dir=None, charmm_params=True, seed=None):
        """Constructor.

           Keyword arguments:

           system        -- The molecular system.
           protocol      -- The protocol for the NAMD process.
           exe           -- The full path to the NAMD executable.
           name          -- The name of the process.
           work_dir      -- The working directory for the process.
           charmm_params -- Whether the parameter file is in CHARMM format.
           seed          -- A random number seed.
        """

        # Call the base class constructor.
        super().__init__(system, protocol, name, work_dir)

        # If the path to the executable wasn't specified, then search
        # for it in $PATH.
        if exe is None:
            self._exe = findExe("namd2").absoluteFilePath()

        else:
            # Make sure executable exists.
            if path.isfile(exe):
                self._exe = protocol
            else:
                raise IOError(('NAMD executable doesn\'t exist: "{x}"').format(x=exe))

        # Set the parameter type.
        self._is_charmm_params = charmm_params

        # The names of the input files.
        self._psf_file = "%s/%s.psf" % (self._work_dir, name)
        self._pdb_file = "%s/%s.pdb" % (self._work_dir, name)
        self._param_file = "%s/%s.params" % (self._work_dir, name)
        self._velocity_file = None
        self._restraint_file = None

        # Set the path for the NAMD configuration file.
        # The 'protocol' argument may contain the path to a custom file.

        # Generate the path name.
        if not self._is_custom:
            self._namd_file = "%s/%s.namd" % (self._work_dir, name)

        # The user has supplied a custom config file.
        else:
            # Make sure the file exists.
            if path.isfile(protocol):
                self._namd_file = protocol
            else:
                raise IOError(('NAMD configuration file doesn\'t exist: "{x}"').format(x=namd_file))

        # Create the list of input files.
        self._input_files = [self._namd_file, self._psf_file, self._pdb_file, self._param_file]

        # Now set up the working directory for the process.
        self._setup()

    @property
    def is_charmm_params(self):
        """Return whether the parameters are in CHARMM format."""
        return self._is_charmm_params

    @is_charmm_params.setter
    def is_charmm_params(self, charmm_params):
        """Set the parameter type."""

        if type(charmm_params) is bool:
            self._is_charmm_params = charmm_params

        else:
            warn("Non-boolean CHARMM parameter flag. Defaulting to True!")
            self._is_charmm_params = True

    def _setup(self):
        """Setup the input files and working directory ready for simulation."""

        # Create the input files...

        # PSF and parameter files.
        psf = CharmmPSF(self._system)
        psf.writeToFile(self._psf_file)

        # PDB file.
        pdb = PDB2(self._system)
        pdb.writeToFile(self._pdb_file)

        # Try to write a PDB "velocity" restart file.
        # The file will only be generated if all atoms in self._system have
        # a "velocity" property.

        # First generate a name for the velocity file.
        velocity_file = os.path.splitext(self._pdb_file)[0] + ".vel"

        # Write the velocity file.
        has_velocities = pdb.writeVelocityFile(velocity_file)

        # If a file was written, store the name of the file and update the
        # list of input files.
        if has_velocities:
            self._velocity_file = velocity_file
            self._input_files.append(self._velocity_file)

        # NAMD requires donor, acceptor, and non-bonded exclusion record entries
        # in the PSF file. We check that these are present and append blank
        # records if not. The records are actually redundant (they have no
        # affect on the MD) so could be stripped (or zeroed) by the CharmmPSF
        # parser.
        has_donors = False
        has_acceptors = False
        has_non_bonded = False

        # Open the PSF file for reading.
        with open(self._psf_file) as f:

            # Read the next line.
            line = f.readline()

            # There are donor records.
            if "!NDON" in line:
                has_donors = True

            # There are acceptor records.
            elif "NACC" in line:
                has_acceptors = True

            # There are non-bonded exclusion records.
            elif "NNB" in line:
                has_non_bonded = True

        # Append empty donor record.
        if not has_donors:
            f = open(self._psf_file, "a")
            f.write("\n%8d !NDON: donors\n" % 0)
            f.close()

        # Append empty acceptor record.
        if not has_acceptors:
            f = open(self._psf_file, "a")
            f.write("\n%8d !NACC: acceptors\n" % 0)
            f.close()

        # Append empty non-bonded exclusion record.
        if not has_acceptors:
            f = open(self._psf_file, "a")
            f.write("\n%8d !NNB: excluded\n" % 0)
            f.close()

        # Generate the NAMD configuration file.
        # Skip if the user has passed a custom config.
        if not self._is_custom:
            self._generate_config_file()

        # Return the list of input files.
        return self._input_files

    def _generate_config_file(self):
        """Generate a NAMD configuration file."""

        # Flag that the system doesn't contain a box.
        has_box = False

        # Check whether the system contains periodic box information.
        if 'space' in self._system.propertyKeys():
            # Flag that we have found a box.
            has_box = True

            # Get the box size and origin.
            box_size = self._system.property('space').dimensions()
            origin   = self._system.property('space').getBoxCenter(Vector(0))

        # Work out the box from the atomic coordinates.
        else:
            box_size, origin, has_water = process._compute_box_size(self._system)

        # Open the configuration file for writing.
        f = open(self._namd_file, "w")

        # Write generic configuration variables.

        # Topology.
        f.write("structure             %s.psf\n" % self._name)
        f.write("coordinates           %s.pdb\n" % self._name)

        # Velocities.
        if not self._velocity_file is None:
            f.write("velocities            %s.vel\n" % self._name)

        # Parameters.
        if self._is_charmm_params:
            f.write("paraTypeCharmm        on\n")
        f.write("parameters            %s.params\n" % self._name)

        # Random number seed.
        if not self._seed is None:
            f.write("seed                  %s\n" % self._seed)

        # Exclusion policy.
        f.write("exclude               scaled1-4\n")

        # Non-bonded potential parameters.

        # Gas phase.
        if not has_water:
            f.write("cutoff                999.\n")
            f.write("zeroMomentum          yes\n")
            f.write("switching             off\n")

        # Solvated.
        else:
            # Only use a cutoff if the box is large enough.
            if min(box_size) > 26:
                f.write("cutoff                12.\n")
                f.write("pairlistdist          14.\n")
                f.write("switching             on\n")
                f.write("switchdist            10.\n")

        # For now, we only set the cell properties if the system contains a
        # box, or is solvated.
        if has_box or has_water:
            # Periodic boundary conditions.
            f.write("cellBasisVector1     %.1f   0.    0.\n" % box_size[0])
            f.write("cellBasisVector2      0.   %.1f   0.\n" % box_size[1])
            f.write("cellBasisVector3      0.    0.   %.1f\n" % box_size[2])
            f.write("cellOrigin            %.1f   %.1f   %.1f\n" % origin)
            f.write("wrapAll               on\n")

            # Periodic electrostatics.
            f.write("PME                   yes\n")
            f.write("PMEGridSpacing        1.\n")

        # Output file parameters.
        f.write("outputName            %s_out\n" % self._name)
        f.write("binaryOutput          no\n")
        f.write("binaryRestart         no\n")

        # Output frequency.
        f.write("restartfreq           500\n")
        f.write("dcdunitcell           no\n")
        f.write("xstFreq               500\n")

        # Printing frequency.
        f.write("outputEnergies        100\n")
        f.write("outputTiming          1000\n")

        # Add configuration variables for a minimisation simulation.
        if self._protocol.type() == ProtocolType.MINIMISATION:
            f.write("temperature           %s\n" % self._protocol.temperature)
            f.write("minimize              %s\n" % self._protocol.steps)

        # Add configuration variables for an equilibration simulation.
        elif self._protocol.type() == ProtocolType.EQUILIBRATION:
            # Set the Tcl temperature variable.
            f.write("set temperature       %s\n" % self._protocol.temperature_target)
            f.write("temperature           $temperature\n")

            # Integrator parameters.
            f.write("timestep              2.\n")
            f.write("rigidBonds            all\n")
            f.write("nonbondedFreq         1\n")
            f.write("fullElectFrequency    2\n")

            # Constant temperature control.
            f.write("langevin              on\n")
            f.write("langevinDamping       1.\n")
            f.write("langevinTemp          $temperature\n")
            f.write("langevinHydrogen      no\n")

            # Constant pressure control.
            if not self._protocol.isConstantTemp():
                f.write("langevinPiston        on\n")
                f.write("langevinPistonTarget  1.01325\n")
                f.write("langevinPistonPeriod  100.\n")
                f.write("langevinPistonDecay   50.\n")
                f.write("langevinPistonTemp    $temperature\n")
                f.write("useGroupPressure      yes\n")
                f.write("useFlexibleCell       no\n")
                f.write("useConstantArea       no\n")

            # Restrain the backbone.
            if self._protocol.is_restrained:
                # Create a restrained system.
                restrained = process._restrain_backbone(self._system)

                # Create a PDB object, mapping the "occupancy" property to "restrained".
                p = PDB2(restrained, {"occupancy" : "restrained"})

                # File name for the restraint file.
                self._restraint_file = "%s/%s.restrained" % (self._work_dir, self._name)

                # Write the PDB file.
                p.writeToFile(self._restraint_file)

                # Update the configuration file.
                f.write("fixedAtoms            yes\n")
                f.write("fixedAtomsFile        %s.restrained\n" % self._name)

            # Work out number of steps needed to exceed desired running time.
            steps = ceil(self._protocol.runtime / 0.002)

            # Heating/cooling simulation.
            if not self._protocol.isConstantTemp():
                # Work out temperature step size (assuming a unit increment).
                denom = abs(self._protocol.temperature_target - self._protocol.temperature_start)
                freq = floor(steps / denom)

                f.write("reassignFreq          %s\n" % freq)
                f.write("reassignTemp          %s\n" % self._protocol.temperature_start)
                f.write("reassignIncr          1.\n")
                f.write("reassignHold          %s\n" % self._protocol.temperature_target)

            # Run the simulation.
            f.write("run                   %s\n" % steps)

        # Close the configuration file.
        f.close()

    def start(self):
        """Start the NAMD simulation."""

        # Store the current working directory.
        dir = os.getcwd()

        # Change to the working directory for the process.
        # This avoid problems with relative paths.
        os.chdir(self._work_dir)

        # Write the command-line process to a README.txt file.
        with open("README.txt", "w") as f:

            # Set the command-line string.
            self._command = "%s %s.namd" % (self._exe, self._name)

            # Write the command to file.
            f.write("# NAMDProcess was run with the following command:\n")
            f.write("%s\n" % self._command)

        # Start the timer.
        self._timer = timer()

        # Start the simulation.
        self._process = Process.run(self._exe, "%s.namd" % self._name,
                                               "%s.out"  % self._name,
                                               "%s.err"  % self._name)

        # Change back to the original working directory.
        os.chdir(dir)

    def getSystem(self):
        """Get the latest molecular configuration as a Sire system."""

        # Read the PDB coordinate file and construct a parameterised molecular
        # system using the original PSF and param files.

        is_coor = False

        # First check for final configuration.
        if path.isfile("%s/%s_out.coor" % (self._work_dir, self._name)):
            file = "%s/%s_out.coor" % (self._work_dir, self._name)
            is_coor = True

        # Otherwise check for a restart file.
        elif path.isfile("%s/%s_out.restart.coor" % (self._work_dir, self._name)):
            file = "%s/%s_out.restart.coor" % (self._work_dir, self._name)
            is_coor = True

        # We found a coordinate file.
        if is_coor:
            # List of files.
            files = [ file, self._psf_file, self._param_file ]

            # Create and return the molecular system.
            return MoleculeParser.read(files)

        else:
            return None

    def getTimeStep(self):
        """Get the current time step."""
        return self._get_nrg_record('TS')

    def getBondEnergy(self):
        """Get the bond energy."""
        return self._get_nrg_record('BOND')

    def getAngleEnergy(self):
        """Get the angle energy."""
        return self._get_nrg_record('ANGLE')

    def getDihedralEnergy(self):
        """Get the dihedral energy."""
        return self._get_nrg_record('DIHED')

    def getImproperEnergy(self):
        """Get the improper energy."""
        return self._get_nrg_record('IMPRP')

    def getElectrostaticEnergy(self):
        """Get the electrostatic energy."""
        return self._get_nrg_record('ELECT')

    def getVanDerWaalsEnergy(self):
        """Get the Van der Vaals energy."""
        return self._get_nrg_record('VDW')

    def getBoundaryEnergy(self):
        """Get the boundary energy."""
        return self._get_nrg_record('BOUNDARY')

    def getMiscEnergy(self):
        """Get the external energy."""
        return self._get_nrg_record('MISC')

    def getKineticEnergy(self):
        """Get the kinetic energy."""
        return self._get_nrg_record('KINETIC')

    def getPotentialEnergy(self):
        """Get the potential energy."""
        return self._get_nrg_record('POTENTIAL')

    def getTotalEnergy(self):
        """Get the total energy."""
        return self._get_nrg_record('TOTAL')

    def getTotal2Energy(self):
        """Get the total energy. (Better KE conservation.)"""
        return self._get_nrg_record('TOTAL2')

    def getTotal3Energy(self):
        """Get the total energy. (Smaller short-time fluctuations.)"""
        return self._get_nrg_record('TOTAL3')

    def getTemperature(self):
        """Get the temperature."""
        return self._get_nrg_record('TEMP')

    def getTemperatureAverage(self):
        """Get the average temperature."""
        return self._get_nrg_record('TEMPAVG')

    def getPressure(self):
        """Get the pressure."""
        return self._get_nrg_record('PRESSURE')

    def getPressureAverage(self):
        """Get the average pressure."""
        return self._get_nrg_record('PRESSAVG')

    def getGPressure(self):
        """Get the pressure. (Hydrogens incorporated into bonded atoms.)"""
        return self._get_nrg_record('GPRESSURE')

    def getGPressureAverage(self):
        """Get the average pressure. (Hydrogens incorporated into bonded atoms.)"""
        return self._get_nrg_record('GPRESSAVG')

    def getVolume(self):
        """Get the volume."""
        return self._get_nrg_record('VOLUME')

    def eta(self):
        """Get the estimated time for the process to finish (in minutes)."""

        # Make sure the list of stdout records is up to date.
        # Print the last zero lines, i.e. no output.
        self.stdout(0)

        # Now search backwards through the list to find the last TIMING record.
        for x, record in reversed(list(enumerate(self._stdout))):

            # Split the record using whitespace.
            data = record.split()

            # We've found a TIMING record.
            if len(data) > 0:
                if data[0] == "TIMING:":

                    # Try to find the "hours" record.
                    # If found, return the entry preceeding it.
                    try:
                        return float(data[data.index("hours") - 1]) * 60

                    # No record found.
                    except ValueError:
                        return None

    def _create_energy_dict(self):
        """Helper function to generate a dictionary of energy records from stdout."""

        # Make sure the list of stdout records is up to date.
        # Print the last zero lines, i.e. no output.
        self.stdout(0)

        # Now search backwards through the list to find the last ENERGY record.
        for x, record in reversed(list(enumerate(self._stdout))):

            # Split the record using whitespace.
            data = record.split()

            # We've found an energy record.
            if len(data) > 0:
                if data[0] == "ENERGY:":
                    # Store the record data.
                    nrg_data = data[1:]

                elif data[0] == "ETITLE:":
                    # Store the title data.
                    # This should be above the ENERGY record, so it's safe
                    # to break at this point.
                    nrg_title = data[1:]
                    break

        # Now create the energy record dictionary.

        # If there is a mismatch in the size of the lists, just return.
        if len(nrg_data) is not len(nrg_title):
            return None

        # Update the energy dictionary.
        else:
            nrg_dict = {}

            for title, data in zip(nrg_title, nrg_data):
                nrg_dict[title] = data

            return nrg_dict

    def _get_nrg_record(self, key):
        """Helper function to get an energy record from the dictionary."""

        # Get the dictionary of current energy records.
        nrg_dict = self._create_energy_dict()

        # No data!
        if nrg_dict is None:
            return None

        # Try to find the key.
        try:
            if key is 'TS':
                return int(nrg_dict[key])
            else:
                return float(nrg_dict[key])

        except KeyError:
            return None
