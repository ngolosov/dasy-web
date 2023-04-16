from geopandas import read_file, sjoin, GeoDataFrame
from pandas.api.types import is_numeric_dtype
from json import loads
from os.path import join
from os import remove
from tempfile import mkdtemp
from shutil import make_archive
from distutils.dir_util import remove_tree


def list_fields(shp_file_location):
    """ function to list numeric fields of the shape file"""
    try:
        dataframe = read_file(str(shp_file_location))
        return list(dataframe._get_numeric_data().columns)
    except:
        return []


def is_polygon(shp_file_location):
    """ function to check if the shape file is polygon type"""
    try:
        dataframe = read_file(str(shp_file_location))
        if str(dataframe.geom_type[0]) == 'Polygon':
            return True
        else:
            return False
    except:
        return False


def dasymetric_map(census_shp_location, footprints_shp_location, pop_field, bldg_height_field, method_3d):
    # reading the files
    try:
        census = read_file(census_shp_location)
        footprints = read_file(footprints_shp_location)
    except:
        return False, 'Error during reading the files'
    # checking if the fields names from the input parameters are correct. Checking the population field first
    if pop_field in census.columns:
        if not is_numeric_dtype(census[pop_field]):
            return False, 'Specified population field is not numeric'
    else:
        return False, 'Specified population field is missing'
    # then, if we asked for 3d dasymetric method, checking the validity of the height field
    if method_3d:
        if bldg_height_field in footprints.columns:
            if not is_numeric_dtype(footprints[bldg_height_field]):
                return False, 'Specified building height field is not numeric'
        else:
            return False, 'Specified building height field is missing'

    # creating id column to use for groupby when say OBJECTID is missing
    census['id'] = census.index

    # reprojecting input files to the same coordinate system, here it is UTM15N
    # but should be replaced with something worlwide
    census = census.to_crs({'init': 'epsg:32615'})
    footprints = footprints.to_crs({'init': 'epsg:32615'})

    # performing spatial join
    joined = sjoin(footprints, census, op='within')
    # calculating and extracting area attribute for footprints
    joined['area'] = joined.area

    if method_3d:
        joined['area'] = joined['area'] * joined[bldg_height_field]

        # calculating accumulated area for all footprints within census polygon
    area_summary = joined.groupby('id')['area'].sum()

    # joining the accumulated area results to the main table
    joined = joined.join(area_summary, on='id', lsuffix='', rsuffix='_sum')

    # calculating population density and population counts
    joined['ppl_per_meas'] = joined[pop_field] / joined['area_sum']
    joined['pop_count'] = joined['ppl_per_meas'] * joined['area']

    # removing all attributes besides specified
    for key in joined.keys():
        if key not in ('geometry', 'pop_count'):
            joined.pop(key)

    joined = joined.to_crs({'init': 'epsg:4326'})

    # writing the output to the json file
    # joined.to_file("countries.geojson", driver='GeoJSON')
    return True, joined.to_json()


def export_as_shp(json):
    """ function to export specified JSON string as zipped shp file. It returns content of the zip file"""
    try:
        # creating temp dir
        temp_dir = mkdtemp()
        # creating geopandas dataframe from json
        data_frame = GeoDataFrame.from_features(loads(json), crs={'init': 'epsg:4326'})
        # saving data frame as SHP file
        data_frame.to_file(join(temp_dir, "output.shp"))
        # creating zip archive from the temporary folder contents
        archive_path = make_archive(temp_dir, "zip", temp_dir)
        # reading the file contents to the variable
        with open(archive_path, mode="rb") as file:
            file_content = file.read()
        # clean up
        remove_tree(temp_dir)
        remove(archive_path)
        return file_content
    except:
        return False
