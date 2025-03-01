from typing import Dict, List

import graphene
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.template.defaultfilters import slugify
from graphene.types import InputObjectType

from ....product import models
from ....product.tasks import (
    update_product_minimal_variant_price_task,
    update_products_minimal_variant_prices_of_catalogues_task,
    update_variants_names,
)
from ....product.thumbnails import (
    create_category_background_image_thumbnails,
    create_collection_background_image_thumbnails,
    create_product_thumbnails,
)
from ....product.utils.attributes import get_name_from_attributes
from ...core.enums import TaxRateType
from ...core.mutations import (
    BaseMutation,
    ClearMetaBaseMutation,
    ModelDeleteMutation,
    ModelMutation,
    UpdateMetaBaseMutation,
)
from ...core.scalars import Decimal, WeightScalar
from ...core.types import SeoInput, Upload
from ...core.utils import (
    clean_seo_fields,
    from_global_id_strict_type,
    validate_image_file,
)
from ...core.utils.reordering import perform_reordering
from ..types import (
    Attribute,
    Category,
    Collection,
    MoveProductInput,
    Product,
    ProductImage,
    ProductVariant,
)
from ..utils import attributes_to_json


class CategoryInput(graphene.InputObjectType):
    description = graphene.String(description="Category description (HTML/text).")
    description_json = graphene.JSONString(description="Category description (JSON).")
    name = graphene.String(description="Category name.")
    slug = graphene.String(description="Category slug.")
    seo = SeoInput(description="Search engine optimization fields.")
    background_image = Upload(description="Background image file.")
    background_image_alt = graphene.String(description="Alt text for an image.")
    testCustomField = graphene.String(description="Add this custom field") #Add custom field

class CategoryCreate(ModelMutation):
    class Arguments:
        input = CategoryInput(
            required=True, description="Fields required to create a category."
        )
        parent_id = graphene.ID(
            description="""
                ID of the parent category. If empty, category will be top level
                category.""",
            name="parent",
        )

    class Meta:
        description = "Creates a new category."
        model = models.Category
        permissions = ("product.manage_products",)

    @classmethod
    def clean_input(cls, info, instance, data):
        cleaned_input = super().clean_input(info, instance, data)
        if "slug" not in cleaned_input and "name" in cleaned_input:
            cleaned_input["slug"] = slugify(cleaned_input["name"])
        parent_id = data["parent_id"]
        if parent_id:
            parent = cls.get_node_or_error(
                info, parent_id, field="parent", only_type=Category
            )
            cleaned_input["parent"] = parent
        if data.get("background_image"):
            image_data = info.context.FILES.get(data["background_image"])
            validate_image_file(image_data, "background_image")
        clean_seo_fields(cleaned_input)
        return cleaned_input

    @classmethod
    def perform_mutation(cls, root, info, **data):
        parent_id = data.pop("parent_id", None)
        data["input"]["parent_id"] = parent_id
        return super().perform_mutation(root, info, **data)

    @classmethod
    def save(cls, info, instance, cleaned_input):
        instance.save()
        if cleaned_input.get("background_image"):
            create_category_background_image_thumbnails.delay(instance.pk)


class CategoryUpdate(CategoryCreate):
    class Arguments:
        id = graphene.ID(required=True, description="ID of a category to update.")
        input = CategoryInput(
            required=True, description="Fields required to update a category."
        )

    class Meta:
        description = "Updates a category."
        model = models.Category
        permissions = ("product.manage_products",)


class CategoryDelete(ModelDeleteMutation):
    class Arguments:
        id = graphene.ID(required=True, description="ID of a category to delete.")

    class Meta:
        description = "Deletes a category."
        model = models.Category
        permissions = ("product.manage_products",)


class CollectionInput(graphene.InputObjectType):
    is_published = graphene.Boolean(
        description="Informs whether a collection is published."
    )
    name = graphene.String(description="Name of the collection.")
    slug = graphene.String(description="Slug of the collection.")
    description = graphene.String(
        description="Description of the collection (HTML/text)."
    )
    description_json = graphene.JSONString(
        description="Description of the collection (JSON)."
    )
    background_image = Upload(description="Background image file.")
    background_image_alt = graphene.String(description="Alt text for an image.")
    seo = SeoInput(description="Search engine optimization fields.")
    publication_date = graphene.Date(description="Publication date. ISO 8601 standard.")

class CollectionCreateInput(CollectionInput):
    products = graphene.List(
        graphene.ID,
        description="List of products to be added to the collection.",
        name="products",
    )


class CollectionCreate(ModelMutation):
    class Arguments:
        input = CollectionCreateInput(
            required=True, description="Fields required to create a collection."
        )

    class Meta:
        description = "Creates a new collection."
        model = models.Collection
        permissions = ("product.manage_products",)

    @classmethod
    def clean_input(cls, info, instance, data):
        cleaned_input = super().clean_input(info, instance, data)
        if "slug" not in cleaned_input and "name" in cleaned_input:
            cleaned_input["slug"] = slugify(cleaned_input["name"])
        if data.get("background_image"):
            image_data = info.context.FILES.get(data["background_image"])
            validate_image_file(image_data, "background_image")
        clean_seo_fields(cleaned_input)
        return cleaned_input

    @classmethod
    def save(cls, info, instance, cleaned_input):
        instance.save()
        if cleaned_input.get("background_image"):
            create_collection_background_image_thumbnails.delay(instance.pk)


class CollectionUpdate(CollectionCreate):
    class Arguments:
        id = graphene.ID(required=True, description="ID of a collection to update.")
        input = CollectionInput(
            required=True, description="Fields required to update a collection."
        )

    class Meta:
        description = "Updates a collection."
        model = models.Collection
        permissions = ("product.manage_products",)

    @classmethod
    def save(cls, info, instance, cleaned_input):
        if cleaned_input.get("background_image"):
            create_collection_background_image_thumbnails.delay(instance.pk)
        instance.save()


class CollectionDelete(ModelDeleteMutation):
    class Arguments:
        id = graphene.ID(required=True, description="ID of a collection to delete.")

    class Meta:
        description = "Deletes a collection."
        model = models.Collection
        permissions = ("product.manage_products",)


class CollectionReorderProducts(BaseMutation):
    collection = graphene.Field(
        Collection, description="Collection from which products are reordered."
    )

    class Meta:
        description = "Reorder the products of a collection"
        permissions = ("product.manage_products",)

    class Arguments:
        collection_id = graphene.Argument(
            graphene.ID, required=True, description="ID of a collection."
        )
        moves = graphene.List(
            MoveProductInput,
            required=True,
            description="The collection products position operations.",
        )

    @classmethod
    def perform_mutation(cls, _root, info, collection_id, moves):
        pk = from_global_id_strict_type(
            info, collection_id, only_type=Collection, field="collection_id"
        )

        try:
            collection = models.Collection.objects.prefetch_related(
                "collectionproduct"
            ).get(pk=pk)
        except ObjectDoesNotExist:
            raise ValidationError(
                {"collection_id": f"Couldn't resolve to a collection: {collection_id}"}
            )

        m2m_related_field = collection.collectionproduct

        operations = {}

        # Resolve the products
        for move_info in moves:
            product_pk = from_global_id_strict_type(
                info, move_info.product_id, only_type=Product, field="moves"
            )

            try:
                m2m_info = m2m_related_field.get(product_id=int(product_pk))
            except ObjectDoesNotExist:
                raise ValidationError(
                    {"moves": f"Couldn't resolve to a product: {move_info.product_id}"}
                )
            operations[m2m_info.pk] = move_info.sort_order

        with transaction.atomic():
            perform_reordering(m2m_related_field, operations)
        return CollectionReorderProducts(collection=collection)


class CollectionAddProducts(BaseMutation):
    collection = graphene.Field(
        Collection, description="Collection to which products will be added."
    )

    class Arguments:
        collection_id = graphene.Argument(
            graphene.ID, required=True, description="ID of a collection."
        )
        products = graphene.List(
            graphene.ID, required=True, description="List of product IDs."
        )

    class Meta:
        description = "Adds products to a collection."
        permissions = ("product.manage_products",)

    @classmethod
    @transaction.atomic()
    def perform_mutation(cls, _root, info, collection_id, products):
        collection = cls.get_node_or_error(
            info, collection_id, field="collection_id", only_type=Collection
        )
        products = cls.get_nodes_or_error(products, "products", Product)
        collection.products.add(*products)
        if collection.sale_set.exists():
            # Updated the db entries, recalculating discounts of affected products
            update_products_minimal_variant_prices_of_catalogues_task.delay(
                product_ids=[p.pk for p in products]
            )
        return CollectionAddProducts(collection=collection)


class CollectionRemoveProducts(BaseMutation):
    collection = graphene.Field(
        Collection, description="Collection from which products will be removed."
    )

    class Arguments:
        collection_id = graphene.Argument(
            graphene.ID, required=True, description="ID of a collection."
        )
        products = graphene.List(
            graphene.ID, required=True, description="List of product IDs."
        )

    class Meta:
        description = "Remove products from a collection."
        permissions = ("product.manage_products",)

    @classmethod
    def perform_mutation(cls, _root, info, collection_id, products):
        collection = cls.get_node_or_error(
            info, collection_id, field="collection_id", only_type=Collection
        )
        products = cls.get_nodes_or_error(products, "products", only_type=Product)
        collection.products.remove(*products)
        if collection.sale_set.exists():
            # Updated the db entries, recalculating discounts of affected products
            update_products_minimal_variant_prices_of_catalogues_task.delay(
                product_ids=[p.pk for p in products]
            )
        return CollectionRemoveProducts(collection=collection)


class CollectionUpdateMeta(UpdateMetaBaseMutation):
    class Meta:
        model = models.Collection
        description = "Update public metadata for Collection"
        permissions = ("product.manage_products",)
        public = True


class CollectionClearMeta(ClearMetaBaseMutation):
    class Meta:
        model = models.Collection
        description = "Clears public metadata item for Collection"
        permissions = ("product.manage_products",)
        public = True


class CollectionUpdatePrivateMeta(UpdateMetaBaseMutation):
    class Meta:
        model = models.Collection
        description = "Update public metadata for Collection"
        permissions = ("product.manage_products",)
        public = False


class CollectionClearPrivateMeta(ClearMetaBaseMutation):
    class Meta:
        model = models.Collection
        description = "Clears public metadata item for Collection"
        permissions = ("product.manage_products",)
        public = False


class CategoryUpdateMeta(UpdateMetaBaseMutation):
    class Meta:
        model = models.Category
        description = "Update public metadata for category"
        permissions = ("product.manage_products",)
        public = True


class CategoryClearMeta(ClearMetaBaseMutation):
    class Meta:
        model = models.Category
        description = "Clears public metadata item for category"
        permissions = ("product.manage_products",)
        public = True


class CategoryUpdatePrivateMeta(UpdateMetaBaseMutation):
    class Meta:
        model = models.Category
        description = "Update public metadata for category"
        permissions = ("product.manage_products",)
        public = False


class CategoryClearPrivateMeta(ClearMetaBaseMutation):
    class Meta:
        model = models.Category
        description = "Clears public metadata item for category"
        permissions = ("product.manage_products",)
        public = False


class AttributeValueInput(InputObjectType):
    id = graphene.ID(description="ID of an attribute")
    name = graphene.String(
        description="Slug of an attribute",
        deprecation_reason="name is deprecated, use id instead",
    )
    slug = graphene.String(description="Slug of an attribute.")
    values = graphene.List(
        graphene.String,
        required=True,
        description=(
            "The value or slug of an attribute to resolve. "
            "If the passed value is non-existent, it will be created."
        ),
    )


class ProductInput(graphene.InputObjectType):
    attributes = graphene.List(AttributeValueInput, description="List of attributes.")
    publication_date = graphene.types.datetime.Date(
        description="Publication date. ISO 8601 standard."
    )
    category = graphene.ID(description="ID of the product's category.", name="category")
    charge_taxes = graphene.Boolean(
        description="Determine if taxes are being charged for the product."
    )
    collections = graphene.List(
        graphene.ID,
        description="List of IDs of collections that the product belongs to.",
        name="collections",
    )
    description = graphene.String(description="Product description (HTML/text).")
    description_json = graphene.JSONString(description="Product description (JSON).")
    is_published = graphene.Boolean(
        description="Determines if product is visible to customers."
    )
    name = graphene.String(description="Product name.")
    price = Decimal(
        description="""
        Product price. Note: this field is deprecated, use basePrice instead."""
    )
    base_price = Decimal(description="Product price.")
    tax_rate = TaxRateType(
        description="Tax rate.", deprecation_reason="taxRate is deprecated, Use taxCode"
    )
    tax_code = graphene.String(description="Tax rate for enabled tax gateway")
    seo = SeoInput(description="Search engine optimization fields.")
    weight = WeightScalar(description="Weight of the Product.", required=False)
    sku = graphene.String(
        description="""Stock keeping unit of a product. Note: this
        field is only used if a product doesn't use variants."""
    )
    quantity = graphene.Int(
        description="""The total quantity of a product available for
        sale. Note: this field is only used if a product doesn't
        use variants."""
    )
    track_inventory = graphene.Boolean(
        description="""Determines if the inventory of this product
        should be tracked. If false, the quantity won't change when customers
        buy this item. Note: this field is only used if a product doesn't
        use variants."""
    )


class ProductCreateInput(ProductInput):
    product_type = graphene.ID(
        description="ID of the type that product belongs to.",
        name="productType",
        required=True,
    )


class ProductCreate(ModelMutation):
    class Arguments:
        input = ProductCreateInput(
            required=True, description="Fields required to create a product."
        )

    class Meta:
        description = "Creates a new product."
        model = models.Product
        permissions = ("product.manage_products",)

    @classmethod
    def clean_input(cls, info, instance, data):
        cleaned_input = super().clean_input(info, instance, data)
        # Attributes are provided as list of `AttributeValueInput` objects.
        # We need to transform them into the format they're stored in the
        # `Product` model, which is HStore field that maps attribute's PK to
        # the value's PK.

        attributes = cleaned_input.pop("attributes", [])
        product_type = (
            instance.product_type if instance.pk else cleaned_input.get("product_type")
        )

        # Try to get price from "basePrice" or "price" field. Once "price" is removed
        # from the schema, only "basePrice" should be used here.
        price = data.get("base_price", data.get("price"))
        if price is not None:
            cleaned_input["price_amount"] = price
            if instance.minimal_variant_price_amount is None:
                # Set the default "minimal_variant_price" to the "price"
                cleaned_input["minimal_variant_price_amount"] = price

        # FIXME  tax_rate logic should be dropped after we remove tax_rate from input
        tax_rate = cleaned_input.pop("tax_rate", "")
        if tax_rate:
            info.context.extensions.assign_tax_code_to_object_meta(instance, tax_rate)

        tax_code = cleaned_input.pop("tax_code", "")
        if tax_code:
            info.context.extensions.assign_tax_code_to_object_meta(instance, tax_code)

        if attributes and product_type:
            qs = product_type.product_attributes.prefetch_related("values")
            try:
                attributes = attributes_to_json(attributes, qs)
            except ValueError as e:
                raise ValidationError({"attributes": str(e)})
            else:
                cleaned_input["attributes"] = attributes
        clean_seo_fields(cleaned_input)
        cls.clean_sku(product_type, cleaned_input)
        return cleaned_input

    @classmethod
    def clean_sku(cls, product_type, cleaned_input):
        """Validate SKU input field.

        When creating products that don't use variants, SKU is required in
        the input in order to create the default variant underneath.
        See the documentation for `has_variants` field for details:
        http://docs.getsaleor.com/en/latest/architecture/products.html#product-types
        """
        if product_type and not product_type.has_variants:
            input_sku = cleaned_input.get("sku")
            if not input_sku:
                raise ValidationError({"sku": "This field cannot be blank."})
            elif models.ProductVariant.objects.filter(sku=input_sku).exists():
                raise ValidationError({"sku": "Product with this SKU already exists."})

    @classmethod
    @transaction.atomic
    def save(cls, info, instance, cleaned_input):
        instance.save()
        if not instance.product_type.has_variants:
            site_settings = info.context.site.settings
            track_inventory = cleaned_input.get(
                "track_inventory", site_settings.track_inventory_by_default
            )
            quantity = cleaned_input.get("quantity", 0)
            sku = cleaned_input.get("sku")
            models.ProductVariant.objects.create(
                product=instance,
                track_inventory=track_inventory,
                sku=sku,
                quantity=quantity,
            )

    @classmethod
    def _save_m2m(cls, info, instance, cleaned_data):
        collections = cleaned_data.get("collections", None)
        if collections is not None:
            instance.collections.set(collections)


class ProductUpdate(ProductCreate):
    class Arguments:
        id = graphene.ID(required=True, description="ID of a product to update.")
        input = ProductInput(
            required=True, description="Fields required to update a product."
        )

    class Meta:
        description = "Updates an existing product."
        model = models.Product
        permissions = ("product.manage_products",)

    @classmethod
    def clean_sku(cls, product_type, cleaned_input):
        input_sku = cleaned_input.get("sku")
        if (
            not product_type.has_variants
            and input_sku
            and models.ProductVariant.objects.filter(sku=input_sku).exists()
        ):
            raise ValidationError({"sku": "Product with this SKU already exists."})

    @classmethod
    @transaction.atomic
    def save(cls, info, instance, cleaned_input):
        instance.save()
        if not instance.product_type.has_variants:
            variant = instance.variants.first()
            update_fields = []
            if "track_inventory" in cleaned_input:
                variant.track_inventory = cleaned_input["track_inventory"]
                update_fields.append("track_inventory")
            if "quantity" in cleaned_input:
                variant.quantity = cleaned_input["quantity"]
                update_fields.append("quantity")
            if "sku" in cleaned_input:
                variant.sku = cleaned_input["sku"]
                update_fields.append("sku")
            if update_fields:
                variant.save(update_fields=update_fields)
        # Recalculate the "minimal variant price"
        update_product_minimal_variant_price_task.delay(instance.pk)


class ProductDelete(ModelDeleteMutation):
    class Arguments:
        id = graphene.ID(required=True, description="ID of a product to delete.")

    class Meta:
        description = "Deletes a product."
        model = models.Product
        permissions = ("product.manage_products",)


class ProductUpdateMeta(UpdateMetaBaseMutation):
    class Meta:
        model = models.Product
        description = "Update public metadata for product"
        permissions = ("product.manage_products",)
        public = True


class ProductClearMeta(ClearMetaBaseMutation):
    class Meta:
        description = "Clears public metadata item for product"
        model = models.Product
        permissions = ("product.manage_products",)
        public = True


class ProductUpdatePrivateMeta(UpdateMetaBaseMutation):
    class Meta:
        description = "Update public metadata for product"
        model = models.Product
        permissions = ("product.manage_products",)
        public = False


class ProductClearPrivateMeta(ClearMetaBaseMutation):
    class Meta:
        description = "Clears public metadata item for product"
        model = models.Product
        permissions = ("product.manage_products",)
        public = False


class ProductVariantInput(graphene.InputObjectType):
    attributes = graphene.List(
        AttributeValueInput,
        required=False,
        description="List of attributes specific to this variant.",
    )
    cost_price = Decimal(description="Cost price of the variant.")
    price_override = Decimal(description="Special price of the particular variant.")
    sku = graphene.String(description="Stock keeping unit.")
    quantity = graphene.Int(
        description="The total quantity of this variant available for sale."
    )
    track_inventory = graphene.Boolean(
        description="""Determines if the inventory of this variant should
               be tracked. If false, the quantity won't change when customers
               buy this item."""
    )
    weight = WeightScalar(description="Weight of the Product Variant.", required=False)


class ProductVariantCreateInput(ProductVariantInput):
    attributes = graphene.List(
        AttributeValueInput,
        required=True,
        description="List of attributes specific to this variant.",
    )
    product = graphene.ID(
        description="Product ID of which type is the variant.",
        name="product",
        required=True,
    )


class ProductVariantCreate(ModelMutation):
    class Arguments:
        input = ProductVariantCreateInput(
            required=True, description="Fields required to create a product variant."
        )

    class Meta:
        description = "Creates a new variant for a product"
        model = models.ProductVariant
        permissions = ("product.manage_products",)

    @classmethod
    def clean_product_type_attributes(cls, info, attributes_qs, attributes_input):
        # transform attributes_input list to a dict of slug:value pairs
        input_slug_map = {}  # type: Dict[str, List[str]]
        input_id_map = {}  # type: Dict[int, List[str]]

        for attr_input in attributes_input:
            attr_id = attr_input.get("id", None)
            slug = attr_input.get("slug", None)
            values = attr_input["values"]

            if attr_id:
                attr_id = from_global_id_strict_type(
                    info, attr_id, only_type=Attribute, field="attributes"
                )
                input_id_map[int(attr_id)] = values
            elif slug:
                input_slug_map[slug] = values
            else:
                raise ValidationError(
                    {"attributes": "Please provide a value's identifier."}
                )

        for attr in attributes_qs:
            values_by_id = input_id_map.get(attr.id, None)
            values_by_slug = input_slug_map.get(attr.slug, None)

            if not values_by_id and not values_by_slug:
                fieldname = "attributes:%s" % attr.slug
                raise ValidationError({fieldname: "This field cannot be blank."})

    @classmethod
    def clean_input(cls, info, instance, data):
        cleaned_input = super().clean_input(info, instance, data)

        cost_price_amount = cleaned_input.pop("cost_price", None)
        if cost_price_amount is not None:
            cleaned_input["cost_price_amount"] = cost_price_amount

        price_override_amount = cleaned_input.pop("price_override", None)
        if price_override_amount is not None:
            cleaned_input["price_override_amount"] = price_override_amount

        # Attributes are provided as list of `AttributeValueInput` objects.
        # We need to transform them into the format they're stored in the
        # `Product` model, which is HStore field that maps attribute's PK to
        # the value's PK.

        if "attributes" in data:
            attributes_input = cleaned_input.pop("attributes")
            product = instance.product if instance.pk else cleaned_input.get("product")
            product_type = product.product_type
            variant_attrs = product_type.variant_attributes.prefetch_related("values")
            try:
                cls.clean_product_type_attributes(info, variant_attrs, attributes_input)
                attributes = attributes_to_json(attributes_input, variant_attrs)
            except ValueError as e:
                raise ValidationError({"attributes": str(e)})
            else:
                cleaned_input["attributes"] = attributes

        return cleaned_input

    @classmethod
    def save(cls, info, instance, cleaned_input):
        attributes = instance.product.product_type.variant_attributes.prefetch_related(
            "values__translations"
        )
        instance.name = get_name_from_attributes(instance, attributes)
        instance.save()
        # Recalculate the "minimal variant price" for the parent product
        update_product_minimal_variant_price_task.delay(instance.product_id)


class ProductVariantUpdate(ProductVariantCreate):
    class Arguments:
        id = graphene.ID(
            required=True, description="ID of a product variant to update."
        )
        input = ProductVariantInput(
            required=True, description="Fields required to update a product variant."
        )

    class Meta:
        description = "Updates an existing variant for product"
        model = models.ProductVariant
        permissions = ("product.manage_products",)


class ProductVariantDelete(ModelDeleteMutation):
    class Arguments:
        id = graphene.ID(
            required=True, description="ID of a product variant to delete."
        )

    class Meta:
        description = "Deletes a product variant."
        model = models.ProductVariant
        permissions = ("product.manage_products",)

    @classmethod
    def success_response(cls, instance):
        # Update the "minimal_variant_prices" of the parent product
        update_product_minimal_variant_price_task.delay(instance.product_id)
        return super().success_response(instance)


class ProductVariantUpdateMeta(UpdateMetaBaseMutation):
    class Meta:
        model = models.ProductVariant
        description = "Update public metadata for product variant"
        permissions = ("product.manage_products",)
        public = True


class ProductVariantClearMeta(ClearMetaBaseMutation):
    class Meta:
        model = models.ProductVariant
        description = "Clears public metadata item for product variant"
        permissions = ("product.manage_products",)
        public = True


class ProductVariantUpdatePrivateMeta(UpdateMetaBaseMutation):
    class Meta:
        model = models.ProductVariant
        description = "Update public metadata for product variant"
        permissions = ("product.manage_products",)
        public = False


class ProductVariantClearPrivateMeta(ClearMetaBaseMutation):
    class Meta:
        model = models.ProductVariant
        description = "Clears public metadata item for product variant"
        permissions = ("product.manage_products",)
        public = False


class ProductTypeInput(graphene.InputObjectType):
    name = graphene.String(description="Name of the product type.")
    has_variants = graphene.Boolean(
        description="""Determines if product of this type has multiple
        variants. This option mainly simplifies product management
        in the dashboard. There is always at least one variant created under
        the hood."""
    )
    product_attributes = graphene.List(
        graphene.ID,
        description="List of attributes shared among all product variants.",
        name="productAttributes",
    )
    variant_attributes = graphene.List(
        graphene.ID,
        description="""List of attributes used to distinguish between
        different variants of a product.""",
        name="variantAttributes",
    )
    is_shipping_required = graphene.Boolean(
        description="""Determines if shipping is required for products
        of this variant."""
    )
    is_digital = graphene.Boolean(
        description="Determines if products are digital.", required=False
    )
    weight = WeightScalar(description="Weight of the ProductType items.")
    tax_rate = TaxRateType(
        description="Tax rate.",
        deprecation_reason=("taxRate is deprecated, Use taxCode"),
    )
    tax_code = graphene.String(description="Tax rate for enabled tax gateway")


class ProductTypeCreate(ModelMutation):
    class Arguments:
        input = ProductTypeInput(
            required=True, description="Fields required to create a product type."
        )

    class Meta:
        description = "Creates a new product type."
        model = models.ProductType
        permissions = ("product.manage_products",)

    @classmethod
    def clean_input(cls, info, instance, data):
        cleaned_input = super().clean_input(info, instance, data)

        # FIXME  tax_rate logic should be dropped after we remove tax_rate from input
        tax_rate = cleaned_input.pop("tax_rate", "")
        if tax_rate:
            if "taxes" not in instance.meta:
                instance.meta["taxes"] = {}
            instance.meta["taxes"]["vatlayer"] = {
                "code": tax_rate,
                "description": tax_rate,
            }
            info.context.extensions.assign_tax_code_to_object_meta(instance, tax_rate)

        tax_code = cleaned_input.pop("tax_code", "")
        if tax_code:
            info.context.extensions.assign_tax_code_to_object_meta(instance, tax_code)

        return cleaned_input

    @classmethod
    def _save_m2m(cls, info, instance, cleaned_data):
        super()._save_m2m(info, instance, cleaned_data)
        if "product_attributes" in cleaned_data:
            instance.product_attributes.set(cleaned_data["product_attributes"])
        if "variant_attributes" in cleaned_data:
            instance.variant_attributes.set(cleaned_data["variant_attributes"])


class ProductTypeUpdate(ProductTypeCreate):
    class Arguments:
        id = graphene.ID(required=True, description="ID of a product type to update.")
        input = ProductTypeInput(
            required=True, description="Fields required to update a product type."
        )

    class Meta:
        description = "Updates an existing product type."
        model = models.ProductType
        permissions = ("product.manage_products",)

    @classmethod
    def save(cls, info, instance, cleaned_input):
        variant_attr = cleaned_input.get("variant_attributes")
        if variant_attr:
            variant_attr = set(variant_attr)
            variant_attr_ids = [attr.pk for attr in variant_attr]
            update_variants_names.delay(instance.pk, variant_attr_ids)
        super().save(info, instance, cleaned_input)


class ProductTypeDelete(ModelDeleteMutation):
    class Arguments:
        id = graphene.ID(required=True, description="ID of a product type to delete.")

    class Meta:
        description = "Deletes a product type."
        model = models.ProductType
        permissions = ("product.manage_products",)


class ProductTypeUpdateMeta(UpdateMetaBaseMutation):
    class Meta:
        model = models.ProductType
        description = "Update public metadata for product type"
        permissions = ("product.manage_products",)
        public = True


class ProductTypeClearMeta(ClearMetaBaseMutation):
    class Meta:
        description = "Clears public metadata item for product type"
        model = models.ProductType
        permissions = ("product.manage_products",)
        public = True


class ProductTypeUpdatePrivateMeta(UpdateMetaBaseMutation):
    class Meta:
        description = "Update public metadata for product type"
        model = models.ProductType
        permissions = ("product.manage_products",)
        public = False


class ProductTypeClearPrivateMeta(ClearMetaBaseMutation):
    class Meta:
        description = "Clears public metadata item for product type"
        model = models.ProductType
        permissions = ("product.manage_products",)
        public = False


class ProductImageCreateInput(graphene.InputObjectType):
    alt = graphene.String(description="Alt text for an image.")
    image = Upload(
        required=True, description="Represents an image file in a multipart request."
    )
    product = graphene.ID(
        required=True, description="ID of an product.", name="product"
    )


class ProductImageCreate(BaseMutation):
    product = graphene.Field(Product)
    image = graphene.Field(ProductImage)

    class Arguments:
        input = ProductImageCreateInput(
            required=True, description="Fields required to create a product image."
        )

    class Meta:
        description = """Create a product image. This mutation must be
        sent as a `multipart` request. More detailed specs of the upload format
        can be found here:
        https://github.com/jaydenseric/graphql-multipart-request-spec"""
        permissions = ("product.manage_products",)

    @classmethod
    def perform_mutation(cls, _root, info, **data):
        data = data.get("input")
        product = cls.get_node_or_error(
            info, data["product"], field="product", only_type=Product
        )
        image_data = info.context.FILES.get(data["image"])
        validate_image_file(image_data, "image")

        image = product.images.create(image=image_data, alt=data.get("alt", ""))
        create_product_thumbnails.delay(image.pk)
        return ProductImageCreate(product=product, image=image)


class ProductImageUpdateInput(graphene.InputObjectType):
    alt = graphene.String(description="Alt text for an image.")


class ProductImageUpdate(BaseMutation):
    product = graphene.Field(Product)
    image = graphene.Field(ProductImage)

    class Arguments:
        id = graphene.ID(required=True, description="ID of a product image to update.")
        input = ProductImageUpdateInput(
            required=True, description="Fields required to update a product image."
        )

    class Meta:
        description = "Updates a product image."
        permissions = ("product.manage_products",)

    @classmethod
    def perform_mutation(cls, _root, info, **data):
        image = cls.get_node_or_error(info, data.get("id"), only_type=ProductImage)
        product = image.product
        alt = data.get("input").get("alt")
        if alt is not None:
            image.alt = alt
            image.save(update_fields=["alt"])
        return ProductImageUpdate(product=product, image=image)


class ProductImageReorder(BaseMutation):
    product = graphene.Field(Product)
    images = graphene.List(ProductImage)

    class Arguments:
        product_id = graphene.ID(
            required=True,
            description="Id of product that images order will be altered.",
        )
        images_ids = graphene.List(
            graphene.ID,
            required=True,
            description="IDs of a product images in the desired order.",
        )

    class Meta:
        description = "Changes ordering of the product image."
        permissions = ("product.manage_products",)

    @classmethod
    def perform_mutation(cls, _root, info, product_id, images_ids):
        product = cls.get_node_or_error(
            info, product_id, field="product_id", only_type=Product
        )
        if len(images_ids) != product.images.count():
            raise ValidationError({"order": "Incorrect number of image IDs provided."})

        images = []
        for image_id in images_ids:
            image = cls.get_node_or_error(
                info, image_id, field="order", only_type=ProductImage
            )
            if image and image.product != product:
                raise ValidationError(
                    {"order": "Image %(image_id)s does not belong to this product."},
                    params={"image_id": image_id},
                )
            images.append(image)

        for order, image in enumerate(images):
            image.sort_order = order
            image.save(update_fields=["sort_order"])

        return ProductImageReorder(product=product, images=images)


class ProductImageDelete(BaseMutation):
    product = graphene.Field(Product)
    image = graphene.Field(ProductImage)

    class Arguments:
        id = graphene.ID(required=True, description="ID of a product image to delete.")

    class Meta:
        description = "Deletes a product image."
        permissions = ("product.manage_products",)

    @classmethod
    def perform_mutation(cls, _root, info, **data):
        image = cls.get_node_or_error(info, data.get("id"), only_type=ProductImage)
        image_id = image.id
        image.delete()
        image.id = image_id
        return ProductImageDelete(product=image.product, image=image)


class VariantImageAssign(BaseMutation):
    product_variant = graphene.Field(ProductVariant)
    image = graphene.Field(ProductImage)

    class Arguments:
        image_id = graphene.ID(
            required=True, description="ID of a product image to assign to a variant."
        )
        variant_id = graphene.ID(required=True, description="ID of a product variant.")

    class Meta:
        description = "Assign an image to a product variant"
        permissions = ("product.manage_products",)

    @classmethod
    def perform_mutation(cls, _root, info, image_id, variant_id):
        image = cls.get_node_or_error(
            info, image_id, field="image_id", only_type=ProductImage
        )
        variant = cls.get_node_or_error(
            info, variant_id, field="variant_id", only_type=ProductVariant
        )
        if image and variant:
            # check if the given image and variant can be matched together
            image_belongs_to_product = variant.product.images.filter(
                pk=image.pk
            ).first()
            if image_belongs_to_product:
                image.variant_images.create(variant=variant)
            else:
                raise ValidationError(
                    {"image_id": "This image doesn't belong to that product."}
                )
        return VariantImageAssign(product_variant=variant, image=image)


class VariantImageUnassign(BaseMutation):
    product_variant = graphene.Field(ProductVariant)
    image = graphene.Field(ProductImage)

    class Arguments:
        image_id = graphene.ID(
            required=True,
            description="ID of a product image to unassign from a variant.",
        )
        variant_id = graphene.ID(required=True, description="ID of a product variant.")

    class Meta:
        description = "Unassign an image from a product variant"
        permissions = ("product.manage_products",)

    @classmethod
    def perform_mutation(cls, _root, info, image_id, variant_id):
        image = cls.get_node_or_error(
            info, image_id, field="image_id", only_type=ProductImage
        )
        variant = cls.get_node_or_error(
            info, variant_id, field="variant_id", only_type=ProductVariant
        )

        try:
            variant_image = models.VariantImage.objects.get(
                image=image, variant=variant
            )
        except models.VariantImage.DoesNotExist:
            raise ValidationError(
                {"image_id": "Image is not assigned to this variant."}
            )
        else:
            variant_image.delete()

        return VariantImageUnassign(product_variant=variant, image=image)
