# __init__.py (lciafmt)
# !/usr/bin/env python3
# coding=utf-8
"""
Public API for lciafmt. Standardizes the format and flows of life cycle
impact assessment (LCIA) data and optionally applies flow mappings as defined
in the Federal LCA Commons Elementary Flow List.
"""

import json
import pkg_resources

import pandas as pd

import lciafmt.cache as cache
import lciafmt.fmap as fmap
import lciafmt.jsonld as jsonld
import lciafmt.traci as traci
import lciafmt.recipe as recipe
import lciafmt.fedefl_inventory as fedefl_inventory
import lciafmt.util as util
import lciafmt.endpoint as ep


from enum import Enum


class Method(Enum):
    """LCIAFormatter Method object with available metadata."""

    TRACI = "TRACI 2.1"
    RECIPE_2016 = "ReCiPe 2016"
    FEDEFL_INV = "FEDEFL Inventory"
    ImpactWorld = "ImpactWorld"

    def get_metadata(cls):
        """Return the stored metadata."""
        metadata = supported_methods()
        for m in metadata:
            if 'case_insensitivity' in m:
                if m['case_insensitivity'] == 'True':
                    m['case_insensitivity'] = True
                else:
                    m['case_insensitivity'] = False
            if m['id'] == cls.name:
                return m

    def get_filename(cls) -> str:
        """Generate standard filename from method name."""
        filename = cls.get_metadata()['name'].replace(" ", "_")
        return filename

    def get_path(cls) -> str:
        """Return category folder name for local storage."""
        path = cls.get_metadata()['path']
        return path

    def get_class(name: str):
        """Parse method_id from passed string and returns method object."""
        for n, c in Method.__members__.items():
            m = c.get_metadata()
            mapping = None
            methods = {}
            if 'mapping' in m:
                mapping = m['mapping']
            if 'methods' in m:
                methods = m['methods']
            if n == name or c.value == name or mapping == name or name in methods.keys():
                return c
        util.log.error('Method not found')


def supported_methods() -> list:
    """Return a list of dictionaries of supported method meta data."""
    json_file = pkg_resources.resource_filename("lciafmt", 'data/methods.json')
    with open(json_file, "r", encoding="utf-8") as f:
        return json.load(f)


def get_method(method_id, add_factors_for_missing_contexts=True,
               endpoint=True, summary=False, file=None, subset=None,
               url=None) -> pd.DataFrame:
    """Generate the method from source in standard format.

    The IDs of supported methods can be obtained using `supported_methods` or
    directly use the constants defined in the Method enumeration type.
    :param method_id: class Method or str, based on id field of
        supported_methods
    :param add_factors_for_missing_contexts: bool, if True applies
        lciafmt.util.aggregate_factors_for_primary_contexts to generate average
        factors for unspecified contexts
    :param endpoint: bool, pass-through for RECIPE_2016, if True generates
        endpoint indicators from midpoints
    :param summary: bool, pass-through for RECIPE_2016, if True aggregates
        endpoint methods into summary indicators
    :param subset: pass-through for FEDEFL_INV, a list of dictionary keys from
        available inventory methods in fedelemflowlist, if none provided all
        available methods will be generated
    :param file: str, alternate filepath for method, defaults to file stored
        in cache
    :param url: str, alternate url for method, defaults to url in method config
    :return: DataFrame of method in standard format
    """
    method_id = util.check_as_class(method_id)
    if method_id == Method.TRACI:
        return traci.get(add_factors_for_missing_contexts, file=file, url=None)
    if method_id == Method.RECIPE_2016:
        return recipe.get(add_factors_for_missing_contexts, endpoint, summary,
                          file=file, url=url)
    if method_id == Method.ImpactWorld:
        import lciafmt.iw as impactworld
        return impactworld.get(file=file, url=url)
    if method_id == Method.FEDEFL_INV:
        return fedefl_inventory.get(subset)


def clear_cache():
    """Delete all stored methods in local temporary cache."""
    cache.clear()


def to_jsonld(df: pd.DataFrame, zip_file: str, write_flows=False):
    """Generate a JSONLD file of the methods passed as DataFrame."""
    util.log.info(f"write JSON-LD package to {zip_file}")
    with jsonld.Writer(zip_file) as w:
        w.write(df, write_flows)


def map_flows(df: pd.DataFrame, system=None, mapping=None,
              preserve_unmapped=False, case_insensitive=False) -> pd.DataFrame:
    """Map the flows in a method using a mapping from fedelemflowlist.

    :param system: str, the named mapping file from fedelemflowlist
    :param mapping: df, alternate mapping that meets FEDEFL mapping file
        specifications
    :param preserve_unmapped: bool, if True unmapped flows remain in the method
    :param case_insensitive, bool, if True case is ignored for source flows
    :return: DataFrame of method with mapped flows.
    """
    mapper = fmap.Mapper(df, system=system, mapping=mapping,
                         preserve_unmapped=preserve_unmapped,
                         case_insensitive=case_insensitive)
    return mapper.run()


def supported_mapping_systems() -> list:
    """Return supported mapping systems."""
    return fmap.supported_mapping_systems()


def get_mapped_method(method_id, indicators=None, methods=None) -> pd.DataFrame:
    """Return a mapped method stored as parquet.

    If a mapped method does not exist locally, it is generated.
    :param method_id: class Method or str, based on id field of
        supported_methods
    :param indicators: list, if not None, return only those indicators passed
    :param methods: list, if not None, return only the version of the methods
        passed. Applies only to methods with multiple versions.
    :return: DataFrame of mapped method
    """
    method_id = util.check_as_class(method_id)
    mapped_method = util.read_method(method_id)
    if mapped_method is None:
        util.log.info('generating ' + method_id.name)
        method = get_method(method_id)
        if 'mapping' in method_id.get_metadata():
            mapping_system = method_id.get_metadata()['mapping']
            case_insensitive = method_id.get_metadata()['case_insensitivity']
            if case_insensitive:
                method['Flowable'] = method['Flowable'].str.lower()
            mapped_method = map_flows(method, system=mapping_system,
                                      case_insensitive=case_insensitive)
            mapped_method = util.collapse_indicators(mapped_method)
            util.store_method(mapped_method, method_id)
        else:
            mapped_method = method
    if indicators is not None:
        mapped_method = mapped_method[mapped_method['Indicator'].isin(indicators)]
        if len(mapped_method) == 0:
            util.log.error('indicator not found')
    if methods is not None:
        mapped_method = mapped_method[mapped_method['Method'].isin(methods)]
        if len(mapped_method) == 0:
            util.log.error('specified method not found')
    mapped_method.reset_index(drop=True, inplace=True)
    return mapped_method


def generate_endpoints(file: str, name=None, matching_fields=None) -> pd.DataFrame:
    """Generate an endpoint method for a supplied file based on specs.

    :param file: name of file in data folder, without extension, containing
        endpoint data based on the format specs for endpoint files
    :param name: str, optional str for naming the generated method
    :param matching_fields: list of fields on which to apply unique endpoint
        conversions, if None
    :return: DataFrame of endpoint method
    """
    endpoints = pd.read_csv(util.datapath+"/"+file+".csv")
    if matching_fields is None:
        matching_fields = ['Indicator']
    method = ep.apply_endpoints(endpoints, matching_fields)
    if name is None:
        method['Method'] = file
    else:
        method['Method'] = name
    return method


def supported_indicators(method_id) -> list:
    """Return a list of indicators for the identified method_id."""
    method = util.read_method(method_id)
    if method is not None:
        indicators = set(list(method['Indicator']))
        return list(indicators)
    else:
        return None
